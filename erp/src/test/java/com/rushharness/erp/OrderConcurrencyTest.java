package com.rushharness.erp;

import static org.assertj.core.api.Assertions.assertThat;

import com.rushharness.erp.api.ApiException;
import com.rushharness.erp.orders.OrderRepository;
import com.rushharness.erp.orders.OrderResult;
import com.rushharness.erp.orders.OrdersService;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

/**
 * Real two-thread races against the database.
 *
 * <p>These exercise the service layer directly because the guarantee being tested — the unique key
 * on {@code (owner, operation_id)} plus the conditional version update — lives in the transaction,
 * not in HTTP plumbing. Threads are released by a latch so the race actually overlaps; no sleep is
 * used to make an assertion pass.
 */
@SpringBootTest
class OrderConcurrencyTest {

  private static final String DB_URL =
      "jdbc:h2:file:./target/test-db/concurrency-"
          + UUID.randomUUID()
          + ";AUTO_SERVER=TRUE;DB_CLOSE_DELAY=-1;LOCK_TIMEOUT=10000";

  @DynamicPropertySource
  static void configure(DynamicPropertyRegistry registry) {
    registry.add("spring.datasource.url", () -> DB_URL);
    registry.add("erp.security.service-token", () -> "junit-service-token");
  }

  @Autowired private OrdersService ordersService;
  @Autowired private OrderRepository orderRepository;
  @Autowired private JdbcTemplate jdbc;

  private record Outcome(OrderResult result, RuntimeException failure) {}

  private static byte[] createBody(int quantity) {
    return ("{\"supplier_id\":\"S001\",\"currency\":\"CNY\",\"lines\":["
            + "{\"part_id\":\"P001\",\"quantity\":"
            + quantity
            + ",\"unit_price\":\"25.50\"}],\"note\":\"并发用例\"}")
        .getBytes(StandardCharsets.UTF_8);
  }

  private static byte[] updateBody(int expectedVersion) {
    return ("{\"expected_version\":"
            + expectedVersion
            + ",\"supplier_id\":\"S001\",\"currency\":\"CNY\","
            + "\"lines\":[{\"part_id\":\"P001\",\"quantity\":60,\"unit_price\":\"25.50\"}]}")
        .getBytes(StandardCharsets.UTF_8);
  }

  /** Runs every task at the same instant and collects results or failures. */
  private static List<Outcome> race(List<Callable<OrderResult>> tasks) throws Exception {
    ExecutorService pool = Executors.newFixedThreadPool(tasks.size());
    CountDownLatch ready = new CountDownLatch(tasks.size());
    CountDownLatch go = new CountDownLatch(1);
    List<Future<Outcome>> futures = new ArrayList<>();
    try {
      for (Callable<OrderResult> task : tasks) {
        futures.add(
            pool.submit(
                () -> {
                  ready.countDown();
                  if (!go.await(30, TimeUnit.SECONDS)) {
                    throw new IllegalStateException("start latch never released");
                  }
                  try {
                    return new Outcome(task.call(), null);
                  } catch (RuntimeException failure) {
                    return new Outcome(null, failure);
                  }
                }));
      }
      assertThat(ready.await(30, TimeUnit.SECONDS)).isTrue();
      go.countDown();

      List<Outcome> outcomes = new ArrayList<>();
      for (Future<Outcome> future : futures) {
        outcomes.add(future.get(120, TimeUnit.SECONDS));
      }
      return outcomes;
    } finally {
      pool.shutdownNow();
    }
  }

  private static long succeeded(List<Outcome> outcomes) {
    return outcomes.stream().filter(outcome -> outcome.result() != null).count();
  }

  private static List<RuntimeException> failures(List<Outcome> outcomes) {
    return outcomes.stream()
        .filter(outcome -> outcome.failure() != null)
        .map(Outcome::failure)
        .toList();
  }

  @Test
  void twoThreadsWithTheSameKeyAndBodyProduceExactlyOneOrder() throws Exception {
    String actor = "junit-race-same";
    byte[] body = createBody(50);

    List<Outcome> outcomes =
        race(
            List.of(
                () -> ordersService.create(actor, "op-race-same", body),
                () -> ordersService.create(actor, "op-race-same", body)));

    assertThat(succeeded(outcomes)).isEqualTo(2);
    assertThat(failures(outcomes)).isEmpty();

    String firstId = outcomes.get(0).result().order().orderId();
    String secondId = outcomes.get(1).result().order().orderId();
    assertThat(firstId).isEqualTo(secondId);
    assertThat(outcomes.stream().filter(outcome -> !outcome.result().replayed()).count())
        .as("only one call may perform the business write")
        .isEqualTo(1);
    assertThat(orderRepository.countOrders(actor)).isEqualTo(1);
    assertThat(new String[] {outcomes.get(0).result().order().totalAmount()})
        .containsExactly("1275.00");
  }

  @Test
  void twoThreadsWithTheSameKeyButDifferentContentConflictExactlyOnce() throws Exception {
    String actor = "junit-race-different";

    List<Outcome> outcomes =
        race(
            List.of(
                () -> ordersService.create(actor, "op-race-different", createBody(50)),
                () -> ordersService.create(actor, "op-race-different", createBody(51))));

    assertThat(succeeded(outcomes)).as("exactly one writer wins").isEqualTo(1);
    List<RuntimeException> failures = failures(outcomes);
    assertThat(failures).hasSize(1);
    assertThat(failures.get(0)).isInstanceOf(ApiException.class);
    assertThat(((ApiException) failures.get(0)).code()).isEqualTo("IDEMPOTENCY_CONFLICT");
    assertThat(((ApiException) failures.get(0)).status().value()).isEqualTo(409);
    assertThat(orderRepository.countOrders(actor)).isEqualTo(1);
  }

  @Test
  void twoConcurrentUpdatesFromTheSameVersionLeaveExactlyOneSuccess() throws Exception {
    String actor = "junit-race-update";
    OrderResult created = ordersService.create(actor, "op-race-update-create", createBody(50));
    String orderId = created.order().orderId();

    List<Outcome> outcomes =
        race(
            List.of(
                () -> ordersService.update(actor, "op-race-update-a", orderId, updateBody(1)),
                () -> ordersService.update(actor, "op-race-update-b", orderId, updateBody(1))));

    assertThat(succeeded(outcomes)).isEqualTo(1);
    List<RuntimeException> failures = failures(outcomes);
    assertThat(failures).hasSize(1);
    assertThat(((ApiException) failures.get(0)).code()).isEqualTo("VERSION_CONFLICT");

    assertThat(ordersService.get(actor, orderId).version()).isEqualTo(2);
    assertThat(ordersService.get(actor, orderId).totalAmount()).isEqualTo("1530.00");
    Integer lineCount =
        jdbc.queryForObject(
            "SELECT COUNT(*) FROM order_lines WHERE order_id = ?", Integer.class, orderId);
    assertThat(lineCount).as("整单替换后只应有一行").isEqualTo(1);
  }

  @Test
  void competitiveWritesNeverTouchInventory() throws Exception {
    int before =
        jdbc.queryForObject("SELECT on_hand FROM inventory WHERE part_id = 'P001'", Integer.class);

    List<Outcome> outcomes =
        race(
            List.of(
                () -> ordersService.create("junit-race-stock-a", "op-race-stock-a", createBody(10)),
                () -> ordersService.create("junit-race-stock-b", "op-race-stock-b", createBody(20))));

    assertThat(succeeded(outcomes)).as("两个不同 owner 的并发写都应成功").isEqualTo(2);

    int after =
        jdbc.queryForObject("SELECT on_hand FROM inventory WHERE part_id = 'P001'", Integer.class);
    assertThat(after).isEqualTo(before);
  }
}
