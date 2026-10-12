package com.rushharness.erp.catalog;

import com.rushharness.erp.api.PageResponse;
import com.rushharness.erp.api.Pagination;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.sql.ResultSet;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

/**
 * Read access to suppliers, parts and supply relations.
 *
 * <p>Every value from the caller is bound as a JDBC parameter. Filter fragments are appended only
 * as literal SQL, never interpolated from input, and ORDER BY is fixed so pagination is
 * deterministic.
 */
@Repository
public class CatalogRepository {

  private static final String SUPPLIER_COLUMNS = "supplier_id, name, active, contact";

  private static final RowMapper<SupplierView> SUPPLIER_MAPPER =
      (ResultSet rs, int rowNum) ->
          new SupplierView(
              rs.getString("supplier_id"),
              rs.getString("name"),
              rs.getBoolean("active"),
              rs.getString("contact"));

  private static final RowMapper<PartView> PART_MAPPER =
      (ResultSet rs, int rowNum) ->
          new PartView(
              rs.getString("part_id"),
              rs.getString("sku"),
              rs.getString("name"),
              rs.getString("unit"),
              rs.getBoolean("active"));

  private static final RowMapper<SupplierOffer> OFFER_MAPPER =
      (ResultSet rs, int rowNum) ->
          new SupplierOffer(
              rs.getString("supplier_id"),
              rs.getString("name"),
              money(rs.getBigDecimal("catalog_price")),
              rs.getString("currency"),
              rs.getInt("lead_days"));

  private static final RowMapper<SupplierPartView> SUPPLIER_PART_MAPPER =
      (ResultSet rs, int rowNum) ->
          new SupplierPartView(
              rs.getString("part_id"),
              rs.getString("sku"),
              rs.getString("name"),
              rs.getString("unit"),
              rs.getBoolean("active"),
              money(rs.getBigDecimal("catalog_price")),
              rs.getString("currency"),
              rs.getInt("lead_days"));

  private final JdbcTemplate jdbc;

  public CatalogRepository(JdbcTemplate jdbc) {
    this.jdbc = jdbc;
  }

  /** Two-decimal string, never a float: JSON must not reintroduce binary rounding. */
  private static String money(BigDecimal value) {
    if (value == null) {
      return null;
    }
    return value.setScale(2, RoundingMode.UNNECESSARY).toPlainString();
  }

  public PageResponse<SupplierView> findSuppliers(String q, Boolean active, Pagination pagination) {
    StringBuilder where = new StringBuilder(" WHERE 1 = 1");
    List<Object> params = new ArrayList<>();
    if (q != null && !q.isBlank()) {
      where.append(" AND (LOWER(name) LIKE ? OR LOWER(supplier_id) LIKE ?)");
      String like = "%" + q.toLowerCase() + "%";
      params.add(like);
      params.add(like);
    }
    if (active != null) {
      where.append(" AND active = ?");
      params.add(active);
    }

    long total = count("suppliers", where, params);
    List<Object> pageParams = new ArrayList<>(params);
    pageParams.add(pagination.pageSize());
    pageParams.add(pagination.offset());
    List<SupplierView> items =
        jdbc.query(
            "SELECT "
                + SUPPLIER_COLUMNS
                + " FROM suppliers"
                + where
                + " ORDER BY supplier_id ASC LIMIT ? OFFSET ?",
            SUPPLIER_MAPPER,
            pageParams.toArray());
    return PageResponse.of(items, total, pagination);
  }

  public PageResponse<PartView> findParts(String q, Pagination pagination) {
    StringBuilder where = new StringBuilder(" WHERE 1 = 1");
    List<Object> params = new ArrayList<>();
    if (q != null && !q.isBlank()) {
      where.append(" AND (LOWER(name) LIKE ? OR LOWER(sku) LIKE ?)");
      String like = "%" + q.toLowerCase() + "%";
      params.add(like);
      params.add(like);
    }

    long total = count("parts", where, params);
    List<Object> pageParams = new ArrayList<>(params);
    pageParams.add(pagination.pageSize());
    pageParams.add(pagination.offset());
    List<PartView> items =
        jdbc.query(
            "SELECT part_id, sku, name, unit, active FROM parts"
                + where
                + " ORDER BY part_id ASC LIMIT ? OFFSET ?",
            PART_MAPPER,
            pageParams.toArray());
    return PageResponse.of(items, total, pagination);
  }

  public Optional<PartView> findPart(String partId) {
    List<PartView> rows =
        jdbc.query(
            "SELECT part_id, sku, name, unit, active FROM parts WHERE part_id = ?",
            PART_MAPPER,
            partId);
    return rows.stream().findFirst();
  }

  /** Active suppliers only: a stopped supplier is not an "available" source. */
  public List<SupplierOffer> findAvailableSuppliers(String partId) {
    return jdbc.query(
        "SELECT sp.supplier_id, s.name, sp.catalog_price, sp.currency, sp.lead_days"
            + " FROM supplier_parts sp"
            + " JOIN suppliers s ON s.supplier_id = sp.supplier_id"
            + " WHERE sp.part_id = ? AND s.active = TRUE"
            + " ORDER BY sp.catalog_price ASC, sp.supplier_id ASC",
        OFFER_MAPPER,
        partId);
  }

  public boolean supplierExists(String supplierId) {
    Long count =
        jdbc.queryForObject(
            "SELECT COUNT(*) FROM suppliers WHERE supplier_id = ?", Long.class, supplierId);
    return count != null && count > 0;
  }

  public Optional<SupplierView> findSupplier(String supplierId) {
    List<SupplierView> rows =
        jdbc.query(
            "SELECT " + SUPPLIER_COLUMNS + " FROM suppliers WHERE supplier_id = ?",
            SUPPLIER_MAPPER,
            supplierId);
    return rows.stream().findFirst();
  }

  /** Whether this supplier is allowed to supply this part; the order validator depends on it. */
  public boolean hasSupplyRelation(String supplierId, String partId) {
    Long count =
        jdbc.queryForObject(
            "SELECT COUNT(*) FROM supplier_parts WHERE supplier_id = ? AND part_id = ?",
            Long.class,
            supplierId,
            partId);
    return count != null && count > 0;
  }

  public PageResponse<SupplierPartView> findPartsBySupplier(
      String supplierId, Pagination pagination) {
    String where = " WHERE sp.supplier_id = ?";
    List<Object> params = List.of(supplierId);
    long total = count("supplier_parts sp", where, params);

    List<Object> pageParams = new ArrayList<>(params);
    pageParams.add(pagination.pageSize());
    pageParams.add(pagination.offset());
    List<SupplierPartView> items =
        jdbc.query(
            "SELECT p.part_id, p.sku, p.name, p.unit, p.active,"
                + " sp.catalog_price, sp.currency, sp.lead_days"
                + " FROM supplier_parts sp"
                + " JOIN parts p ON p.part_id = sp.part_id"
                + where
                + " ORDER BY p.part_id ASC LIMIT ? OFFSET ?",
            SUPPLIER_PART_MAPPER,
            pageParams.toArray());
    return PageResponse.of(items, total, pagination);
  }

  private long count(String fromClause, CharSequence where, List<Object> params) {
    Long total =
        jdbc.queryForObject(
            "SELECT COUNT(*) FROM " + fromClause + where, Long.class, params.toArray());
    return total == null ? 0L : total;
  }
}
