package com.rushharness.erp;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

/**
 * Order creation, whole-order update, versioning and idempotent replay over HTTP.
 *
 * <p>Each test owns a distinct actor id, so assertions about "exactly one order" stay local and the
 * tests do not depend on execution order. No test rolls back implicitly: the replay and conflict
 * behaviour only exists once rows are committed.
 */
@SpringBootTest
@AutoConfigureMockMvc
class OrderApiTest {

  private static final String SERVICE_TOKEN = "junit-service-token";
  private static final String DB_URL =
      "jdbc:h2:file:./target/test-db/orders-"
          + UUID.randomUUID()
          + ";AUTO_SERVER=TRUE;DB_CLOSE_DELAY=-1;LOCK_TIMEOUT=10000";

  @DynamicPropertySource
  static void configure(DynamicPropertyRegistry registry) {
    registry.add("spring.datasource.url", () -> DB_URL);
    registry.add("erp.security.service-token", () -> SERVICE_TOKEN);
  }

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @Autowired private JdbcTemplate jdbc;

  private static String createBody(String supplierId, String partId, int quantity, String price) {
    return "{\"supplier_id\":\""
        + supplierId
        + "\",\"currency\":\"CNY\",\"lines\":[{\"part_id\":\""
        + partId
        + "\",\"quantity\":"
        + quantity
        + ",\"unit_price\":\""
        + price
        + "\"}],\"note\":\"演示采购\"}";
  }

  private static String updateBody(int expectedVersion) {
    return "{\"expected_version\":"
        + expectedVersion
        + ",\"supplier_id\":\"S001\",\"currency\":\"CNY\","
        + "\"lines\":[{\"part_id\":\"P001\",\"quantity\":60,\"unit_price\":\"25.50\"}],"
        + "\"note\":\"调整数量\"}";
  }

  private MvcResult postOrder(String actor, String operationId, String body) throws Exception {
    return mockMvc
        .perform(
            post("/api/erp/v1/orders")
                .header("X-Service-Token", SERVICE_TOKEN)
                .header("X-Actor-Id", actor)
                .header("X-Operation-Id", operationId)
                .contentType(MediaType.APPLICATION_JSON)
                .content(body.getBytes(StandardCharsets.UTF_8)))
        .andReturn();
  }

  private MvcResult putOrder(String actor, String operationId, String orderId, String body)
      throws Exception {
    return mockMvc
        .perform(
            put("/api/erp/v1/orders/{id}", orderId)
                .header("X-Service-Token", SERVICE_TOKEN)
                .header("X-Actor-Id", actor)
                .header("X-Operation-Id", operationId)
                .contentType(MediaType.APPLICATION_JSON)
                .content(body.getBytes(StandardCharsets.UTF_8)))
        .andReturn();
  }

  private JsonNode body(MvcResult result) throws Exception {
    return objectMapper.readTree(result.getResponse().getContentAsByteArray());
  }

  private long ordersOf(String actor) {
    Long count =
        jdbc.queryForObject(
            "SELECT COUNT(*) FROM orders WHERE owner_user_id = ?", Long.class, actor);
    return count == null ? 0L : count;
  }

  private int onHand(String partId) {
    Integer value =
        jdbc.queryForObject(
            "SELECT on_hand FROM inventory WHERE part_id = ?", Integer.class, partId);
    return value == null ? -1 : value;
  }

  // ------------------------------------------------------------------ create

  @Test
  void creatingAnOrderComputesTheTotalAndLeavesInventoryAlone() throws Exception {
    String actor = "junit-create";
    MvcResult result = postOrder(actor, "op-junit-create-1", createBody("S001", "P001", 50, "25.50"));

    assertThat(result.getResponse().getStatus()).isEqualTo(201);
    JsonNode data = body(result).get("data");
    assertThat(data.get("total_amount").asText()).isEqualTo("1275.00");
    assertThat(data.get("version").asInt()).isEqualTo(1);
    assertThat(data.get("owner_user_id").asText()).isEqualTo(actor);
    assertThat(data.get("order_no").asText()).startsWith("PO-");
    assertThat(data.get("lines").get(0).get("line_amount").asText()).isEqualTo("1275.00");
    assertThat(result.getResponse().getHeader("Idempotent-Replayed")).isEqualTo("false");

    assertThat(ordersOf(actor)).isEqualTo(1);
    assertThat(onHand("P001")).isEqualTo(8); // ordering never consumes stock in this demo
  }

  @Test
  void multiLineOrderUsesTheFixedTotalOf87() throws Exception {
    String actor = "junit-multiline";
    String body =
        "{\"supplier_id\":\"S001\",\"currency\":\"CNY\",\"lines\":["
            + "{\"part_id\":\"P001\",\"quantity\":2,\"unit_price\":\"25.50\"},"
            + "{\"part_id\":\"P002\",\"quantity\":3,\"unit_price\":\"12.00\"}]}";
    MvcResult result = postOrder(actor, "op-junit-multiline", body);

    assertThat(result.getResponse().getStatus()).isEqualTo(201);
    assertThat(body(result).get("data").get("total_amount").asText()).isEqualTo("87.00");
  }

  @Test
  void theServerIgnoresAnyClientSuppliedTotal() throws Exception {
    String actor = "junit-total";
    String withTotal =
        "{\"supplier_id\":\"S001\",\"currency\":\"CNY\",\"total_amount\":\"1.00\","
            + "\"lines\":[{\"part_id\":\"P001\",\"quantity\":50,\"unit_price\":\"25.50\"}]}";
    MvcResult result = postOrder(actor, "op-junit-total", withTotal);

    assertThat(result.getResponse().getStatus()).isEqualTo(400);
    assertThat(body(result).get("error").get("code").asText()).isEqualTo("INVALID_ARGUMENT");
    assertThat(ordersOf(actor)).isZero();
  }

  @Test
  void theOrderIsRetrievableWithItsLines() throws Exception {
    String actor = "junit-query";
    MvcResult created = postOrder(actor, "op-junit-query", createBody("S001", "P001", 50, "25.50"));
    String orderId = body(created).get("data").get("order_id").asText();

    mockMvc
        .perform(
            get("/api/erp/v1/orders")
                .header("X-Service-Token", SERVICE_TOKEN)
                .header("X-Actor-Id", actor)
                .param("order_id", orderId))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(1))
        .andExpect(jsonPath("$.data.items[0].order_id").value(orderId))
        .andExpect(jsonPath("$.data.items[0].lines[0].part_id").value("P001"))
        .andExpect(jsonPath("$.data.items[0].lines[0].quantity").value(50));
  }

  // ------------------------------------------------------------------ update

  @Test
  void updatingWithTheCurrentVersionReplacesTheWholeOrder() throws Exception {
    String actor = "junit-update";
    MvcResult created = postOrder(actor, "op-junit-update-create", createBody("S001", "P001", 50, "25.50"));
    String orderId = body(created).get("data").get("order_id").asText();

    MvcResult updated = putOrder(actor, "op-junit-update-1", orderId, updateBody(1));

    assertThat(updated.getResponse().getStatus()).isEqualTo(200);
    JsonNode data = body(updated).get("data");
    assertThat(data.get("total_amount").asText()).isEqualTo("1530.00");
    assertThat(data.get("version").asInt()).isEqualTo(2);
    assertThat(data.get("lines").size()).isEqualTo(1);
    assertThat(data.get("lines").get(0).get("quantity").asInt()).isEqualTo(60);
  }

  @Test
  void reusingAStaleVersionIsRejected() throws Exception {
    String actor = "junit-version";
    MvcResult created =
        postOrder(actor, "op-junit-version-create", createBody("S001", "P001", 50, "25.50"));
    String orderId = body(created).get("data").get("order_id").asText();

    assertThat(putOrder(actor, "op-junit-version-1", orderId, updateBody(1)).getResponse().getStatus())
        .isEqualTo(200);

    MvcResult stale = putOrder(actor, "op-junit-version-2", orderId, updateBody(1));
    assertThat(stale.getResponse().getStatus()).isEqualTo(409);
    JsonNode error = body(stale).get("error");
    assertThat(error.get("code").asText()).isEqualTo("VERSION_CONFLICT");
    assertThat(error.get("details").get("current_version").asInt()).isEqualTo(2);
  }

  @Test
  void updatingWithoutExpectedVersionIsRejected() throws Exception {
    String actor = "junit-noversion";
    MvcResult created =
        postOrder(actor, "op-junit-noversion-create", createBody("S001", "P001", 50, "25.50"));
    String orderId = body(created).get("data").get("order_id").asText();

    String body =
        "{\"supplier_id\":\"S001\",\"currency\":\"CNY\","
            + "\"lines\":[{\"part_id\":\"P001\",\"quantity\":60,\"unit_price\":\"25.50\"}]}";
    MvcResult result = putOrder(actor, "op-junit-noversion", orderId, body);

    assertThat(result.getResponse().getStatus()).isEqualTo(400);
    assertThat(body(result).get("error").get("code").asText()).isEqualTo("INVALID_ARGUMENT");
  }

  // ------------------------------------------------------------------ isolation

  @Test
  void anotherUserCannotSeeOrModifyTheOrder() throws Exception {
    String owner = "junit-owner";
    MvcResult created = postOrder(owner, "op-junit-owner", createBody("S001", "P001", 50, "25.50"));
    String orderId = body(created).get("data").get("order_id").asText();

    mockMvc
        .perform(
            get("/api/erp/v1/orders")
                .header("X-Service-Token", SERVICE_TOKEN)
                .header("X-Actor-Id", "demo-b"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(0));

    MvcResult stolen = putOrder("demo-b", "op-junit-steal", orderId, updateBody(1));
    assertThat(stolen.getResponse().getStatus()).isEqualTo(404);
    assertThat(body(stolen).get("error").get("code").asText()).isEqualTo("ORDER_NOT_FOUND");
  }

  @Test
  void orderWritesRequireATrustedActorAndAnOperationId() throws Exception {
    MvcResult noActor =
        mockMvc
            .perform(
                post("/api/erp/v1/orders")
                    .header("X-Service-Token", SERVICE_TOKEN)
                    .header("X-Operation-Id", "op-junit-noactor")
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(createBody("S001", "P001", 1, "25.50").getBytes(StandardCharsets.UTF_8)))
            .andReturn();
    assertThat(noActor.getResponse().getStatus()).isEqualTo(401);

    MvcResult noOperation =
        mockMvc
            .perform(
                post("/api/erp/v1/orders")
                    .header("X-Service-Token", SERVICE_TOKEN)
                    .header("X-Actor-Id", "junit-noop")
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(createBody("S001", "P001", 1, "25.50").getBytes(StandardCharsets.UTF_8)))
            .andReturn();
    assertThat(noOperation.getResponse().getStatus()).isEqualTo(400);
  }

  // ------------------------------------------------------------------ idempotency

  @Test
  void resendingTheSameBytesWithTheSameOperationIdReplaysTheOriginalOrder() throws Exception {
    String actor = "junit-replay";
    String body = createBody("S001", "P001", 50, "25.50");

    MvcResult first = postOrder(actor, "op-junit-replay", body);
    MvcResult second = postOrder(actor, "op-junit-replay", body);

    String firstId = body(first).get("data").get("order_id").asText();
    String secondId = body(second).get("data").get("order_id").asText();

    assertThat(secondId).isEqualTo(firstId);
    assertThat(second.getResponse().getHeader("Idempotent-Replayed")).isEqualTo("true");
    assertThat(ordersOf(actor)).isEqualTo(1);
  }

  @Test
  void reusingAnOperationIdWithDifferentContentIsAConflict() throws Exception {
    String actor = "junit-mismatch";
    postOrder(actor, "op-junit-mismatch", createBody("S001", "P001", 50, "25.50"));

    MvcResult conflicting = postOrder(actor, "op-junit-mismatch", createBody("S001", "P001", 51, "25.50"));

    assertThat(conflicting.getResponse().getStatus()).isEqualTo(409);
    assertThat(body(conflicting).get("error").get("code").asText())
        .isEqualTo("IDEMPOTENCY_CONFLICT");
    assertThat(ordersOf(actor)).isEqualTo(1);
  }

  @Test
  void anOperationIdCannotBeReusedForADifferentOperationType() throws Exception {
    String actor = "junit-cross-type";
    MvcResult created =
        postOrder(actor, "op-junit-cross", createBody("S001", "P001", 50, "25.50"));
    String orderId = body(created).get("data").get("order_id").asText();

    MvcResult misuse = putOrder(actor, "op-junit-cross", orderId, updateBody(1));

    assertThat(misuse.getResponse().getStatus()).isEqualTo(409);
    assertThat(body(misuse).get("error").get("code").asText())
        .isEqualTo("IDEMPOTENCY_CONFLICT");
  }

  // ------------------------------------------------------------------ validation and rollback

  @Test
  void businessRuleViolationsAreRejectedWithoutWritingAnything() throws Exception {
    record Case(String actor, String body, int expectedStatus, String expectedCode) {}

    Case[] cases = {
      new Case(
          "junit-inactive-supplier",
          createBody("S003", "P001", 10, "26.00"),
          422,
          "INACTIVE_SUPPLIER"),
      new Case(
          "junit-unsupported-pair", createBody("S002", "P003", 10, "68.00"), 422, "UNSUPPORTED_PART"),
      new Case("junit-inactive-part", createBody("S001", "P005", 10, "5.00"), 422, "UNSUPPORTED_PART"),
      new Case("junit-unknown-part", createBody("S001", "P999", 10, "5.00"), 404, "PART_NOT_FOUND"),
      new Case(
          "junit-duplicate-part",
          "{\"supplier_id\":\"S001\",\"currency\":\"CNY\",\"lines\":["
              + "{\"part_id\":\"P001\",\"quantity\":1,\"unit_price\":\"25.50\"},"
              + "{\"part_id\":\"P001\",\"quantity\":2,\"unit_price\":\"25.50\"}]}",
          400,
          "INVALID_ARGUMENT"),
      new Case("junit-3dp", createBody("S001", "P001", 1, "25.505"), 400, "INVALID_ARGUMENT"),
      new Case("junit-zero-qty", createBody("S001", "P001", 0, "25.50"), 400, "INVALID_ARGUMENT"),
      new Case(
          "junit-bad-currency",
          "{\"supplier_id\":\"S001\",\"currency\":\"USD\",\"lines\":["
              + "{\"part_id\":\"P001\",\"quantity\":1,\"unit_price\":\"25.50\"}]}",
          400,
          "INVALID_ARGUMENT")
    };

    for (Case testCase : cases) {
      MvcResult result = postOrder(testCase.actor(), "op-" + testCase.actor(), testCase.body());
      assertThat(result.getResponse().getStatus())
          .as("%s -> %s", testCase.actor(), new String(result.getResponse().getContentAsByteArray()))
          .isEqualTo(testCase.expectedStatus());
      assertThat(body(result).get("error").get("code").asText())
          .isEqualTo(testCase.expectedCode());
      assertThat(ordersOf(testCase.actor())).as("zero writes for %s", testCase.actor()).isZero();
      assertThat(
              jdbc.queryForObject(
                  "SELECT COUNT(*) FROM operations WHERE owner_user_id = ?",
                  Long.class,
                  testCase.actor()))
          .as("no ledger row for %s", testCase.actor())
          .isZero();
    }
  }

  @Test
  void afailedUpdateLeavesNoLedgerRowAndCanBeRetriedWithTheSameOperationId() throws Exception {
    String actor = "junit-rollback";
    MvcResult created =
        postOrder(actor, "op-junit-rollback-create", createBody("S001", "P001", 50, "25.50"));
    String orderId = body(created).get("data").get("order_id").asText();

    MvcResult failed = putOrder(actor, "op-junit-rollback", orderId, updateBody(99));
    assertThat(failed.getResponse().getStatus()).isEqualTo(409);
    assertThat(
            jdbc.queryForObject(
                "SELECT COUNT(*) FROM operations WHERE owner_user_id = ? AND operation_id = ?",
                Long.class,
                actor,
                "op-junit-rollback"))
        .as("事务回滚不得留下只写了 Operation 的半成品")
        .isZero();

    // The same operation id must still be usable, because nothing was recorded for it.
    MvcResult retried = putOrder(actor, "op-junit-rollback", orderId, updateBody(1));
    assertThat(retried.getResponse().getStatus()).isEqualTo(200);
    assertThat(body(retried).get("data").get("version").asInt()).isEqualTo(2);
  }

  @Test
  void orderLinesAreNeverLeftBehindWhenAnUpdateFails() throws Exception {
    String actor = "junit-lines";
    MvcResult created = postOrder(actor, "op-junit-lines-create", createBody("S001", "P001", 50, "25.50"));
    String orderId = body(created).get("data").get("order_id").asText();

    assertThat(putOrder(actor, "op-junit-lines-bad", orderId, updateBody(42)).getResponse().getStatus())
        .isEqualTo(409);

    Integer lineCount =
        jdbc.queryForObject(
            "SELECT COUNT(*) FROM order_lines WHERE order_id = ?", Integer.class, orderId);
    assertThat(lineCount).isEqualTo(1);
    assertThat(
            jdbc.queryForObject(
                "SELECT total_amount FROM orders WHERE order_id = ?",
                java.math.BigDecimal.class,
                orderId))
        .isEqualByComparingTo("1275.00");
  }
}
