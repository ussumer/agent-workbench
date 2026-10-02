package com.rushharness.erp.orders;

import com.rushharness.erp.api.PageResponse;
import com.rushharness.erp.api.Pagination;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.sql.ResultSet;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Collectors;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

/**
 * Persistence for orders, their lines and the idempotency ledger.
 *
 * <p>All callers pass bound parameters; no caller supplied value is concatenated into SQL. Ordering
 * is fixed ({@code created_at DESC, order_id DESC}) so paging is deterministic under equal
 * timestamps.
 */
@Repository
public class OrderRepository {

  /** One row of the idempotency ledger. */
  public record OperationRecord(
      String ownerUserId,
      String operationId,
      String payloadHash,
      String operationType,
      String resourceId,
      String responseJson) {}

  private record OrderRow(
      String orderId,
      String orderNo,
      String ownerUserId,
      String supplierId,
      String currency,
      String totalAmount,
      int version,
      Instant createdAt,
      Instant updatedAt,
      String note) {}

  private static final RowMapper<OrderRow> ORDER_ROW_MAPPER =
      (ResultSet rs, int rowNum) ->
          new OrderRow(
              rs.getString("order_id"),
              rs.getString("order_no"),
              rs.getString("owner_user_id"),
              rs.getString("supplier_id"),
              rs.getString("currency"),
              money(rs.getBigDecimal("total_amount")),
              rs.getInt("version"),
              rs.getObject("created_at", OffsetDateTime.class).toInstant(),
              rs.getObject("updated_at", OffsetDateTime.class).toInstant(),
              rs.getString("note"));

  private static final RowMapper<OrderLineView> LINE_MAPPER =
      (ResultSet rs, int rowNum) ->
          new OrderLineView(
              rs.getInt("line_no"),
              rs.getString("part_id"),
              rs.getInt("quantity"),
              money(rs.getBigDecimal("unit_price")),
              money(rs.getBigDecimal("line_amount")));

  private static final RowMapper<OperationRecord> OPERATION_MAPPER =
      (ResultSet rs, int rowNum) ->
          new OperationRecord(
              rs.getString("owner_user_id"),
              rs.getString("operation_id"),
              rs.getString("payload_hash"),
              rs.getString("operation_type"),
              rs.getString("resource_id"),
              rs.getString("response_json"));

  private final JdbcTemplate jdbc;

  public OrderRepository(JdbcTemplate jdbc) {
    this.jdbc = jdbc;
  }

  private static String money(BigDecimal value) {
    return value == null ? null : value.setScale(2, RoundingMode.UNNECESSARY).toPlainString();
  }

  private static OffsetDateTime bind(Instant instant) {
    return OffsetDateTime.ofInstant(instant, ZoneOffset.UTC);
  }

  // ------------------------------------------------------------------ ledger

  public Optional<OperationRecord> findOperation(String ownerUserId, String operationId) {
    List<OperationRecord> rows =
        jdbc.query(
            "SELECT owner_user_id, operation_id, payload_hash, operation_type, resource_id,"
                + " response_json FROM operations WHERE owner_user_id = ? AND operation_id = ?",
            OPERATION_MAPPER,
            ownerUserId,
            operationId);
    return rows.stream().findFirst();
  }

  /**
   * Parks the unique key {@code (owner, operation_id)} before any business write. A concurrent
   * caller with the same key blocks here and then fails, which is what makes the retry safe.
   */
  public void insertOperation(
      String ownerUserId,
      String operationId,
      String payloadHash,
      String operationType,
      String resourceId,
      Instant createdAt) {
    jdbc.update(
        "INSERT INTO operations (owner_user_id, operation_id, payload_hash, operation_type,"
            + " resource_id, response_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ownerUserId,
        operationId,
        payloadHash,
        operationType,
        resourceId,
        "{}",
        bind(createdAt));
  }

  public void storeOperationResponse(
      String ownerUserId, String operationId, String resourceId, String responseJson) {
    jdbc.update(
        "UPDATE operations SET response_json = ?, resource_id = ?"
            + " WHERE owner_user_id = ? AND operation_id = ?",
        responseJson,
        resourceId,
        ownerUserId,
        operationId);
  }

  // ------------------------------------------------------------------ orders

  public long nextOrderSequence() {
    Long value = jdbc.queryForObject("SELECT NEXT VALUE FOR order_number_seq", Long.class);
    return value == null ? 0L : value;
  }

  public void insertOrder(
      String orderId,
      String orderNo,
      String ownerUserId,
      String supplierId,
      String currency,
      BigDecimal totalAmount,
      String note,
      Instant createdAt) {
    jdbc.update(
        "INSERT INTO orders (order_id, order_no, owner_user_id, supplier_id, currency,"
            + " total_amount, version, note, created_at, updated_at)"
            + " VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
        orderId,
        orderNo,
        ownerUserId,
        supplierId,
        currency,
        totalAmount,
        note,
        bind(createdAt),
        bind(createdAt));
  }

  public void insertLines(String orderId, List<LineInsert> lines) {
    List<Object[]> batch = new ArrayList<>();
    for (LineInsert line : lines) {
      batch.add(
          new Object[] {
            orderId,
            line.lineNo(),
            line.partId(),
            line.quantity(),
            line.unitPrice(),
            line.lineAmount()
          });
    }
    jdbc.batchUpdate(
        "INSERT INTO order_lines (order_id, line_no, part_id, quantity, unit_price, line_amount)"
            + " VALUES (?, ?, ?, ?, ?, ?)",
        batch);
  }

  /** Row shape for a line insert; amounts are already validated and scaled to two decimals. */
  public record LineInsert(
      int lineNo, String partId, int quantity, BigDecimal unitPrice, BigDecimal lineAmount) {}

  public void deleteLines(String orderId) {
    jdbc.update("DELETE FROM order_lines WHERE order_id = ?", orderId);
  }

  /**
   * Conditional update: {@code WHERE owner AND order_id AND version}. Zero affected rows means the
   * order is gone, belongs to someone else, or the version moved — the caller distinguishes those.
   */
  public int updateOrderConditional(
      String ownerUserId,
      String orderId,
      int expectedVersion,
      String supplierId,
      String currency,
      BigDecimal totalAmount,
      String note,
      Instant updatedAt) {
    return jdbc.update(
        "UPDATE orders SET supplier_id = ?, currency = ?, total_amount = ?, note = ?,"
            + " version = version + 1, updated_at = ?"
            + " WHERE owner_user_id = ? AND order_id = ? AND version = ?",
        supplierId,
        currency,
        totalAmount,
        note,
        bind(updatedAt),
        ownerUserId,
        orderId,
        expectedVersion);
  }

  public Optional<Integer> findVersion(String ownerUserId, String orderId) {
    List<Integer> rows =
        jdbc.query(
            "SELECT version FROM orders WHERE owner_user_id = ? AND order_id = ?",
            (rs, rowNum) -> rs.getInt("version"),
            ownerUserId,
            orderId);
    return rows.stream().findFirst();
  }

  public PageResponse<OrderView> findOrders(
      String ownerUserId, String orderId, String supplierId, Pagination pagination) {
    StringBuilder where = new StringBuilder(" WHERE o.owner_user_id = ?");
    List<Object> params = new ArrayList<>();
    params.add(ownerUserId);
    if (orderId != null && !orderId.isBlank()) {
      where.append(" AND o.order_id = ?");
      params.add(orderId);
    }
    if (supplierId != null && !supplierId.isBlank()) {
      where.append(" AND o.supplier_id = ?");
      params.add(supplierId);
    }

    Long total =
        jdbc.queryForObject(
            "SELECT COUNT(*) FROM orders o" + where, Long.class, params.toArray());

    List<Object> pageParams = new ArrayList<>(params);
    pageParams.add(pagination.pageSize());
    pageParams.add(pagination.offset());
    List<OrderRow> rows =
        jdbc.query(
            "SELECT o.order_id, o.order_no, o.owner_user_id, o.supplier_id, o.currency,"
                + " o.total_amount, o.version, o.note, o.created_at, o.updated_at"
                + " FROM orders o"
                + where
                + " ORDER BY o.created_at DESC, o.order_id DESC LIMIT ? OFFSET ?",
            ORDER_ROW_MAPPER,
            pageParams.toArray());

    return PageResponse.of(withLines(rows), total == null ? 0L : total, pagination);
  }

  public Optional<OrderView> findOrder(String ownerUserId, String orderId) {
    List<OrderRow> rows =
        jdbc.query(
            "SELECT o.order_id, o.order_no, o.owner_user_id, o.supplier_id, o.currency,"
                + " o.total_amount, o.version, o.note, o.created_at, o.updated_at"
                + " FROM orders o WHERE o.owner_user_id = ? AND o.order_id = ?",
            ORDER_ROW_MAPPER,
            ownerUserId,
            orderId);
    return withLines(rows).stream().findFirst();
  }

  /** Fetches every line for the given orders in one query instead of one query per order. */
  private List<OrderView> withLines(List<OrderRow> rows) {
    if (rows.isEmpty()) {
      return List.of();
    }
    String placeholders = rows.stream().map(row -> "?").collect(Collectors.joining(", "));
    Object[] ids = rows.stream().map(OrderRow::orderId).toArray();
    List<Map<String, Object>> lineRows =
        jdbc.queryForList(
            "SELECT order_id, line_no, part_id, quantity, unit_price, line_amount"
                + " FROM order_lines WHERE order_id IN ("
                + placeholders
                + ") ORDER BY order_id ASC, line_no ASC",
            ids);

    Map<String, List<OrderLineView>> byOrder = new LinkedHashMap<>();
    for (Map<String, Object> row : lineRows) {
      String orderId = (String) row.get("ORDER_ID");
      byOrder
          .computeIfAbsent(orderId, key -> new ArrayList<>())
          .add(
              new OrderLineView(
                  ((Number) row.get("LINE_NO")).intValue(),
                  (String) row.get("PART_ID"),
                  ((Number) row.get("QUANTITY")).intValue(),
                  money((BigDecimal) row.get("UNIT_PRICE")),
                  money((BigDecimal) row.get("LINE_AMOUNT"))));
    }

    List<OrderView> views = new ArrayList<>(rows.size());
    for (OrderRow row : rows) {
      views.add(
          new OrderView(
              row.orderId(),
              row.orderNo(),
              row.ownerUserId(),
              row.supplierId(),
              row.currency(),
              row.totalAmount(),
              row.version(),
              row.createdAt(),
              row.updatedAt(),
              row.note(),
              byOrder.getOrDefault(row.orderId(), List.of())));
    }
    return views;
  }

  public long countOrders(String ownerUserId) {
    Long total =
        jdbc.queryForObject(
            "SELECT COUNT(*) FROM orders WHERE owner_user_id = ?", Long.class, ownerUserId);
    return total == null ? 0L : total;
  }

  public long countOperations(String ownerUserId) {
    Long total =
        jdbc.queryForObject(
            "SELECT COUNT(*) FROM operations WHERE owner_user_id = ?", Long.class, ownerUserId);
    return total == null ? 0L : total;
  }

  /** Used by concurrency tests to assert that no half-written order survives a failed attempt. */
  public long countAllOrders() {
    Long total = jdbc.queryForObject("SELECT COUNT(*) FROM orders", Long.class);
    return total == null ? 0L : total;
  }
}
