package com.rushharness.erp.orders;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.ObjectReader;
import com.rushharness.erp.api.ApiException;
import com.rushharness.erp.api.PageResponse;
import com.rushharness.erp.api.Pagination;
import com.rushharness.erp.catalog.CatalogRepository;
import com.rushharness.erp.catalog.PartView;
import com.rushharness.erp.catalog.SupplierView;
import java.io.IOException;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.regex.Pattern;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Order writes: validation, atomic transactions, optimistic versioning and idempotent replay.
 *
 * <p>The important properties, and where they live:
 *
 * <ul>
 *   <li><b>Owner comes from the trusted header</b>, never from the body.
 *   <li><b>Money is computed here</b> with BigDecimal; the caller's total is not accepted.
 *   <li><b>Payload hash covers the raw received bytes</b>, so a retry must resend the exact same
 *       bytes to be recognised as a replay.
 *   <li><b>One transaction</b> parks the operation key, writes the order, and stores the response.
 *       A failure rolls all of it back, so no half-order and no orphan ledger row survive.
 *   <li><b>Inventory is never touched.</b> This demo only records orders.
 * </ul>
 */
@Service
public class OrdersService {

  public static final String TYPE_CREATE = "order_create";
  public static final String TYPE_UPDATE = "order_update";

  /** Two-decimal money string, 0.01..999999.99; three decimals are rejected, not rounded. */
  private static final Pattern MONEY =
      Pattern.compile("^(?:0\\.(?:0[1-9]|[1-9][0-9])|[1-9][0-9]{0,5}\\.[0-9]{2})$");

  private static final int MAX_LINES = 20;
  private static final int MIN_QUANTITY = 1;
  private static final int MAX_QUANTITY = 10_000;
  private static final int MAX_NOTE_LENGTH = 500;

  private static final DateTimeFormatter DAY =
      DateTimeFormatter.ofPattern("yyyyMMdd").withZone(ZoneOffset.UTC);

  private record PreparedOrder(
      String supplierId,
      String currency,
      BigDecimal totalAmount,
      String note,
      List<OrderRepository.LineInsert> lines) {}

  private final OrderRepository orders;
  private final CatalogRepository catalog;
  private final ObjectMapper objectMapper;
  private final ObjectReader strictReader;
  private final TransactionTemplate transactions;

  public OrdersService(
      OrderRepository orders,
      CatalogRepository catalog,
      ObjectMapper objectMapper,
      PlatformTransactionManager transactionManager) {
    this.orders = orders;
    this.catalog = catalog;
    this.objectMapper = objectMapper;
    // Strict reader: an unknown body property (for example a client-supplied total_amount) is an
    // error, not something to silently ignore.
    this.strictReader =
        objectMapper.reader().with(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES);
    this.transactions = new TransactionTemplate(transactionManager);
  }

  // ------------------------------------------------------------------ queries

  public PageResponse<OrderView> search(
      String ownerUserId, String orderId, String supplierId, Pagination pagination) {
    return orders.findOrders(ownerUserId, orderId, supplierId, pagination);
  }

  public OrderView get(String ownerUserId, String orderId) {
    return orders.findOrder(ownerUserId, orderId)
        .orElseThrow(
            () ->
                ApiException.notFound(
                    "ORDER_NOT_FOUND", "no order " + orderId + " for this owner"));
  }

  // ------------------------------------------------------------------ writes

  public OrderResult create(String ownerUserId, String operationId, byte[] rawBody) {
    String payloadHash = sha256(rawBody);
    Optional<OrderRepository.OperationRecord> existing =
        orders.findOperation(ownerUserId, operationId);
    if (existing.isPresent()) {
      return replay(existing.get(), payloadHash, TYPE_CREATE, null);
    }

    CreateOrderRequest request = readBody(rawBody, CreateOrderRequest.class);
    PreparedOrder prepared =
        validate(request.supplierId(), request.currency(), request.lines(), request.note());

    try {
      OrderResult result =
          transactions.execute(
              status -> {
                long sequence = orders.nextOrderSequence();
                String orderId = "O-" + DAY.format(Instant.now()) + "-" + pad(sequence);
                String orderNo = "PO-" + DAY.format(Instant.now()) + "-" + pad(sequence);
                Instant now = Instant.now();

                // Park the idempotency key first: a concurrent retry blocks here and then fails.
                orders.insertOperation(
                    ownerUserId, operationId, payloadHash, TYPE_CREATE, orderId, now);
                orders.insertOrder(
                    orderId,
                    orderNo,
                    ownerUserId,
                    prepared.supplierId(),
                    prepared.currency(),
                    prepared.totalAmount(),
                    prepared.note(),
                    now);
                orders.insertLines(orderId, prepared.lines());

                OrderView view = requireOrder(ownerUserId, orderId);
                orders.storeOperationResponse(
                    ownerUserId, operationId, orderId, writeJson(view));
                return new OrderResult(view, false);
              });
      return result;
    } catch (DuplicateKeyException duplicateKey) {
      return recoverFromDuplicateKey(ownerUserId, operationId, payloadHash, TYPE_CREATE, null);
    }
  }

  public OrderResult update(
      String ownerUserId, String operationId, String orderId, byte[] rawBody) {
    String payloadHash = sha256(rawBody);
    Optional<OrderRepository.OperationRecord> existing =
        orders.findOperation(ownerUserId, operationId);
    if (existing.isPresent()) {
      return replay(existing.get(), payloadHash, TYPE_UPDATE, orderId);
    }

    UpdateOrderRequest request = readBody(rawBody, UpdateOrderRequest.class);
    if (request.expectedVersion() == null || request.expectedVersion() < 1) {
      throw ApiException.invalidArgument(
          "INVALID_ARGUMENT",
          "expected_version must be an integer >= 1; a fuzzy patch is not supported",
          Map.of("expected_version", String.valueOf(request.expectedVersion())));
    }
    int expectedVersion = request.expectedVersion();
    PreparedOrder prepared =
        validate(request.supplierId(), request.currency(), request.lines(), request.note());

    try {
      OrderResult result =
          transactions.execute(
              status -> {
                Instant now = Instant.now();
                orders.insertOperation(
                    ownerUserId, operationId, payloadHash, TYPE_UPDATE, orderId, now);

                int updated =
                    orders.updateOrderConditional(
                        ownerUserId,
                        orderId,
                        expectedVersion,
                        prepared.supplierId(),
                        prepared.currency(),
                        prepared.totalAmount(),
                        prepared.note(),
                        now);
                if (updated == 0) {
                  Optional<Integer> currentVersion = orders.findVersion(ownerUserId, orderId);
                  if (currentVersion.isEmpty()) {
                    throw ApiException.notFound(
                        "ORDER_NOT_FOUND", "no order " + orderId + " for this owner");
                  }
                  throw ApiException.conflict(
                      "VERSION_CONFLICT",
                      "the order was modified by someone else; read the latest version and approve"
                          + " a new candidate instead of reusing the old approval",
                      Map.of(
                          "expected_version", expectedVersion,
                          "current_version", currentVersion.get()));
                }

                orders.deleteLines(orderId);
                orders.insertLines(orderId, prepared.lines());

                OrderView view = requireOrder(ownerUserId, orderId);
                orders.storeOperationResponse(
                    ownerUserId, operationId, orderId, writeJson(view));
                return new OrderResult(view, false);
              });
      return result;
    } catch (DuplicateKeyException duplicateKey) {
      return recoverFromDuplicateKey(ownerUserId, operationId, payloadHash, TYPE_UPDATE, orderId);
    }
  }

  // ------------------------------------------------------------------ internals

  private OrderView requireOrder(String ownerUserId, String orderId) {
    return orders
        .findOrder(ownerUserId, orderId)
        .orElseThrow(() -> new IllegalStateException("order disappeared inside its transaction"));
  }

  private OrderResult recoverFromDuplicateKey(
      String ownerUserId,
      String operationId,
      String payloadHash,
      String expectedType,
      String expectedResourceId) {
    // The transaction that won the key has committed by the time the duplicate surfaces, so the
    // ledger row is readable and the retry can be answered without repeating the business write.
    Optional<OrderRepository.OperationRecord> committed =
        orders.findOperation(ownerUserId, operationId);
    if (committed.isEmpty()) {
      throw ApiException.conflict(
          "IDEMPOTENCY_CONFLICT",
          "another request with this operation id has not committed yet; retry the same bytes",
          Map.of("operation_id", operationId, "retryable", true));
    }
    return replay(committed.get(), payloadHash, expectedType, expectedResourceId);
  }

  private OrderResult replay(
      OrderRepository.OperationRecord record,
      String payloadHash,
      String expectedType,
      String expectedResourceId) {
    boolean sameType = record.operationType().equals(expectedType);
    boolean sameHash = record.payloadHash().equals(payloadHash);
    boolean sameResource =
        expectedResourceId == null || expectedResourceId.equals(record.resourceId());
    if (!sameType || !sameHash || !sameResource) {
      throw ApiException.conflict(
          "IDEMPOTENCY_CONFLICT",
          "this operation id was already used with different content, type or target",
          Map.of(
              "operation_id", record.operationId(),
              "stored_type", record.operationType(),
              "stored_resource_id", String.valueOf(record.resourceId())));
    }
    try {
      OrderView view = objectMapper.readValue(record.responseJson(), OrderView.class);
      if (view.orderId() == null) {
        throw new IOException("stored response is incomplete");
      }
      return new OrderResult(view, true);
    } catch (IOException failure) {
      throw ApiException.conflict(
          "IDEMPOTENCY_CONFLICT",
          "the stored response for this operation id cannot be replayed",
          Map.of("operation_id", record.operationId()));
    }
  }

  private PreparedOrder validate(
      String supplierId, String currency, List<OrderLineRequest> lines, String note) {
    String normalizedSupplier = require(supplierId, "supplier_id");
    if (!"CNY".equals(currency)) {
      throw ApiException.invalidArgument(
          "INVALID_ARGUMENT",
          "currency must be CNY; no exchange-rate service exists",
          Map.of("currency", String.valueOf(currency)));
    }
    if (lines == null || lines.isEmpty()) {
      throw ApiException.invalidArgument(
          "INVALID_ARGUMENT", "lines must contain between 1 and " + MAX_LINES + " entries", Map.of());
    }
    if (lines.size() > MAX_LINES) {
      throw ApiException.invalidArgument(
          "INVALID_ARGUMENT",
          "an order holds at most " + MAX_LINES + " lines",
          Map.of("lines", lines.size()));
    }
    if (note != null && note.length() > MAX_NOTE_LENGTH) {
      throw ApiException.invalidArgument(
          "INVALID_ARGUMENT",
          "note must be at most " + MAX_NOTE_LENGTH + " characters",
          Map.of("note_length", note.length()));
    }

    SupplierView supplier =
        catalog
            .findSupplier(normalizedSupplier)
            .orElseThrow(
                () ->
                    ApiException.notFound(
                        "SUPPLIER_NOT_FOUND", "no supplier with id " + normalizedSupplier));
    if (!supplier.active()) {
      throw ApiException.unprocessable(
          "INACTIVE_SUPPLIER", "supplier " + normalizedSupplier + " is not active");
    }

    Set<String> seenParts = new LinkedHashSet<>();
    List<OrderRepository.LineInsert> inserts = new ArrayList<>();
    BigDecimal total = BigDecimal.ZERO.setScale(2, RoundingMode.UNNECESSARY);
    int lineNo = 1;

    for (OrderLineRequest line : lines) {
      if (line == null) {
        throw ApiException.invalidArgument("INVALID_ARGUMENT", "each line must be an object", Map.of());
      }
      String partId = require(line.partId(), "part_id");
      if (!seenParts.add(partId)) {
        throw ApiException.invalidArgument(
            "INVALID_ARGUMENT",
            "part " + partId + " appears more than once in this order",
            Map.of("part_id", partId));
      }

      PartView part =
          catalog
              .findPart(partId)
              .orElseThrow(
                  () -> ApiException.notFound("PART_NOT_FOUND", "no part with id " + partId));
      // 停用物料与「无供货关系」对外共用 UNSUPPORTED_PART：契约 422 行只列
      // INACTIVE_SUPPLIER / UNSUPPORTED_PART。消息仍区分两种原因，便于排查。
      if (!part.active()) {
        throw ApiException.unprocessable(
            "UNSUPPORTED_PART", "part " + partId + " is not active");
      }
      if (!catalog.hasSupplyRelation(normalizedSupplier, partId)) {
        throw ApiException.unprocessable(
            "UNSUPPORTED_PART",
            "supplier " + normalizedSupplier + " does not supply part " + partId);
      }

      int quantity = requireQuantity(line.quantity());
      BigDecimal unitPrice = requireMoney(line.unitPrice(), "unit_price");
      BigDecimal lineAmount =
          unitPrice.multiply(BigDecimal.valueOf(quantity)).setScale(2, RoundingMode.UNNECESSARY);
      inserts.add(new OrderRepository.LineInsert(lineNo++, partId, quantity, unitPrice, lineAmount));
      total = total.add(lineAmount);
    }

    return new PreparedOrder(
        normalizedSupplier, "CNY", total.setScale(2, RoundingMode.UNNECESSARY),
        note == null ? "" : note, inserts);
  }

  private static String require(String value, String field) {
    if (value == null || value.isBlank()) {
      throw ApiException.invalidArgument(
          "INVALID_ARGUMENT", field + " is required", Map.of("field", field));
    }
    return value.trim();
  }

  private static int requireQuantity(Integer quantity) {
    if (quantity == null) {
      throw ApiException.invalidArgument(
          "INVALID_ARGUMENT", "quantity is required", Map.of("field", "quantity"));
    }
    if (quantity < MIN_QUANTITY || quantity > MAX_QUANTITY) {
      throw ApiException.invalidArgument(
          "INVALID_ARGUMENT",
          "quantity must be within " + MIN_QUANTITY + ".." + MAX_QUANTITY,
          Map.of("quantity", quantity));
    }
    return quantity;
  }

  private static BigDecimal requireMoney(String value, String field) {
    if (value == null || !MONEY.matcher(value).matches()) {
      throw ApiException.invalidArgument(
          "INVALID_ARGUMENT",
          field + " must be a two-decimal money string between 0.01 and 999999.99",
          Map.of("field", field, "value", String.valueOf(value)));
    }
    return new BigDecimal(value);
  }

  private <T> T readBody(byte[] rawBody, Class<T> type) {
    try {
      return strictReader.forType(type).readValue(rawBody);
    } catch (IOException failure) {
      throw ApiException.invalidArgument(
          "INVALID_ARGUMENT",
          "request body does not match the contract: " + failure.getMessage(),
          Map.of());
    }
  }

  private String writeJson(OrderView view) {
    try {
      return objectMapper.writeValueAsString(view);
    } catch (IOException failure) {
      throw new IllegalStateException("cannot serialise the order response", failure);
    }
  }

  private static String pad(long sequence) {
    return String.format("%08d", sequence);
  }

  /** SHA-256 over the exact bytes received, matching the gateway's frozen payload. */
  public static String sha256(byte[] payload) {
    try {
      MessageDigest digest = MessageDigest.getInstance("SHA-256");
      return HexFormat.of().formatHex(digest.digest(payload));
    } catch (NoSuchAlgorithmException failure) {
      throw new IllegalStateException("SHA-256 is unavailable", failure);
    }
  }

  /** Convenience for callers that hold a string body rather than raw bytes. */
  public static String sha256(String payload) {
    return sha256(payload.getBytes(StandardCharsets.UTF_8));
  }
}
