package com.rushharness.erp.orders;

import com.rushharness.erp.api.ApiException;
import com.rushharness.erp.api.ApiResponse;
import com.rushharness.erp.api.PageResponse;
import com.rushharness.erp.api.Pagination;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * Order endpoints from contracts/erp.md.
 *
 * <p>The actor id is read from the trusted internal header supplied by the gateway. It never comes
 * from the body, and a request without it is rejected rather than guessed. The raw request body is
 * taken as bytes so the idempotency hash covers exactly what arrived on the wire.
 */
@RestController
@RequestMapping("/api/erp/v1")
public class OrderController {

  /** Advertises that the response came from the idempotency ledger rather than a new write. */
  public static final String REPLAYED_HEADER = "Idempotent-Replayed";

  private final OrdersService orders;

  public OrderController(OrdersService orders) {
    this.orders = orders;
  }

  @GetMapping("/orders")
  public ApiResponse<PageResponse<OrderView>> search(
      @RequestHeader(name = "X-Actor-Id", required = false) String actorId,
      @RequestParam(name = "order_id", required = false) String orderId,
      @RequestParam(name = "supplier_id", required = false) String supplierId,
      @RequestParam(name = "page", required = false) Integer page,
      @RequestParam(name = "page_size", required = false) Integer pageSize) {
    Pagination pagination = Pagination.of(page, pageSize);
    return ApiResponse.ok(
        orders.search(requireActor(actorId), orderId, supplierId, pagination));
  }

  @PostMapping("/orders")
  public ResponseEntity<ApiResponse<OrderView>> create(
      @RequestHeader(name = "X-Actor-Id", required = false) String actorId,
      @RequestHeader(name = "X-Operation-Id", required = false) String operationId,
      @RequestBody byte[] body) {
    OrderResult result = orders.create(requireActor(actorId), requireOperationId(operationId), body);
    return ResponseEntity.status(HttpStatus.CREATED)
        .header(REPLAYED_HEADER, Boolean.toString(result.replayed()))
        .body(ApiResponse.ok(result.order()));
  }

  @PutMapping("/orders/{order_id}")
  public ResponseEntity<ApiResponse<OrderView>> update(
      @RequestHeader(name = "X-Actor-Id", required = false) String actorId,
      @RequestHeader(name = "X-Operation-Id", required = false) String operationId,
      @PathVariable("order_id") String orderId,
      @RequestBody byte[] body) {
    OrderResult result =
        orders.update(requireActor(actorId), requireOperationId(operationId), orderId, body);
    return ResponseEntity.ok()
        .header(REPLAYED_HEADER, Boolean.toString(result.replayed()))
        .body(ApiResponse.ok(result.order()));
  }

  private static String requireActor(String actorId) {
    if (actorId == null || actorId.isBlank()) {
      throw ApiException.unauthorized(
          "X-Actor-Id is required; the owner comes from the verified service identity, not the body");
    }
    return actorId.trim();
  }

  private static String requireOperationId(String operationId) {
    if (operationId == null || operationId.isBlank()) {
      throw ApiException.invalidArgument(
          "INVALID_ARGUMENT",
          "X-Operation-Id is required so a retry can be recognised instead of duplicating the order",
          Map.of("header", "X-Operation-Id"));
    }
    return operationId.trim();
  }
}
