package com.rushharness.erp;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.rushharness.erp.config.SeedLoader;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.DefaultApplicationArguments;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.transaction.annotation.Transactional;

/**
 * Catalog and inventory behaviour against a real file-backed H2 database.
 *
 * <p>Each run gets its own database file so the developer's demo database is never touched. The
 * service token is supplied explicitly, which also lets us assert that unauthenticated calls fail.
 */
@SpringBootTest
@AutoConfigureMockMvc
class CatalogApiTest {

  private static final String SERVICE_TOKEN = "junit-service-token";
  private static final String DB_URL =
      "jdbc:h2:file:./target/test-db/catalog-"
          + UUID.randomUUID()
          + ";AUTO_SERVER=TRUE;DB_CLOSE_DELAY=-1";

  @DynamicPropertySource
  static void configure(DynamicPropertyRegistry registry) {
    registry.add("spring.datasource.url", () -> DB_URL);
    registry.add("erp.security.service-token", () -> SERVICE_TOKEN);
  }

  @Autowired private MockMvc mockMvc;
  @Autowired private JdbcTemplate jdbc;
  @Autowired private SeedLoader seedLoader;

  private static MockHttpServletRequestBuilder authed(String url, Object... vars) {
    return get(url, vars)
        .header("X-Service-Token", SERVICE_TOKEN)
        .header("X-Actor-Id", "demo-a")
        .header("X-Request-Id", "req-junit");
  }

  @Test
  void seedIsLoadedFromTheSharedFixtureFile() {
    Integer suppliers = jdbc.queryForObject("SELECT COUNT(*) FROM suppliers", Integer.class);
    Integer inventory = jdbc.queryForObject("SELECT COUNT(*) FROM inventory", Integer.class);
    assertThat(suppliers).isEqualTo(3);
    assertThat(inventory).isEqualTo(5);
  }

  @Test
  void suppliersAreReturnedInStableIdentifierOrder() throws Exception {
    mockMvc
        .perform(authed("/api/erp/v1/suppliers"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.request_id").value("req-junit"))
        .andExpect(jsonPath("$.data.total").value(3))
        .andExpect(jsonPath("$.data.page").value(1))
        .andExpect(jsonPath("$.data.page_size").value(20))
        .andExpect(jsonPath("$.data.items[0].supplier_id").value("S001"))
        .andExpect(jsonPath("$.data.items[1].supplier_id").value("S002"))
        .andExpect(jsonPath("$.data.items[2].supplier_id").value("S003"));
  }

  @Test
  void suppliersCanBeFilteredToActiveOnesOnly() throws Exception {
    mockMvc
        .perform(authed("/api/erp/v1/suppliers").param("active", "true"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(2))
        .andExpect(jsonPath("$.data.items[0].supplier_id").value("S001"))
        .andExpect(jsonPath("$.data.items[1].supplier_id").value("S002"));

    mockMvc
        .perform(authed("/api/erp/v1/suppliers").param("active", "false"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(1))
        .andExpect(jsonPath("$.data.items[0].supplier_id").value("S003"));
  }

  @Test
  void suppliersCanBeSearchedByNameSubstring() throws Exception {
    mockMvc
        .perform(authed("/api/erp/v1/suppliers").param("q", "远航"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(1))
        .andExpect(jsonPath("$.data.items[0].name").value("远航配件"));
  }

  @Test
  void partsCanBeSearchedBySkuSubstring() throws Exception {
    mockMvc
        .perform(authed("/api/erp/v1/parts").param("q", "chain"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(1))
        .andExpect(jsonPath("$.data.items[0].part_id").value("P003"));
  }

  @Test
  void partDetailListsOnlyActiveSuppliersWithTwoDecimalPrices() throws Exception {
    mockMvc
        .perform(authed("/api/erp/v1/parts/{id}", "P001"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.part.sku").value("BRAKE-01"))
        // S002 offers 24.00 and S001 25.50; the inactive S003 must not appear.
        .andExpect(jsonPath("$.data.available_suppliers.length()").value(2))
        .andExpect(jsonPath("$.data.available_suppliers[0].supplier_id").value("S002"))
        .andExpect(jsonPath("$.data.available_suppliers[0].catalog_price").value("24.00"))
        .andExpect(jsonPath("$.data.available_suppliers[1].catalog_price").value("25.50"));
  }

  @Test
  void unknownPartIsNotFound() throws Exception {
    mockMvc
        .perform(authed("/api/erp/v1/parts/{id}", "P999"))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.error.code").value("PART_NOT_FOUND"))
        .andExpect(jsonPath("$.request_id").value("req-junit"));
  }

  @Test
  void unknownSupplierIsNotFound() throws Exception {
    mockMvc
        .perform(authed("/api/erp/v1/suppliers/{id}/parts", "S999"))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.error.code").value("SUPPLIER_NOT_FOUND"));
  }

  @Test
  void supplierWithoutSupplyRelationSimplyOmitsThatPart() throws Exception {
    mockMvc
        .perform(authed("/api/erp/v1/suppliers/{id}/parts", "S002"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(3))
        .andExpect(jsonPath("$.data.items[0].part_id").value("P001"))
        .andExpect(jsonPath("$.data.items[1].part_id").value("P002"))
        .andExpect(jsonPath("$.data.items[2].part_id").value("P004"));
  }

  @Test
  void inventoryWarningsMatchTheFixedSeedExactly() throws Exception {
    mockMvc
        .perform(authed("/api/erp/v1/inventory/warnings"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(3))
        .andExpect(jsonPath("$.data.items[0].part_id").value("P001"))
        .andExpect(jsonPath("$.data.items[0].suggested_quantity").value(42))
        .andExpect(jsonPath("$.data.items[1].part_id").value("P003"))
        .andExpect(jsonPath("$.data.items[1].suggested_quantity").value(15))
        .andExpect(jsonPath("$.data.items[2].part_id").value("P004"))
        .andExpect(jsonPath("$.data.items[2].suggested_quantity").value(30));
  }

  @Test
  @Transactional
  void warningThresholdIsStrictlyLessThan() throws Exception {
    // P002 sits at exactly on_hand 40 with threshold 15, and P005 at 30 with threshold 10:
    // neither may be reported. Setting on_hand equal to the threshold must still not warn.
    jdbc.update("UPDATE inventory SET on_hand = warning_threshold WHERE part_id = 'P003'");
    mockMvc
        .perform(authed("/api/erp/v1/inventory/warnings"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(2))
        .andExpect(jsonPath("$.data.items[0].part_id").value("P001"))
        .andExpect(jsonPath("$.data.items[1].part_id").value("P004"));

    jdbc.update("UPDATE inventory SET on_hand = warning_threshold - 1 WHERE part_id = 'P003'");
    mockMvc
        .perform(authed("/api/erp/v1/inventory/warnings"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(3));
  }

  @Test
  @Transactional
  void warningsFollowTheDatabaseRatherThanAFixedReply() throws Exception {
    jdbc.update("UPDATE inventory SET on_hand = 999 WHERE part_id = 'P001'");
    mockMvc
        .perform(authed("/api/erp/v1/inventory/warnings"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(2))
        .andExpect(jsonPath("$.data.items[0].part_id").value("P003"));
  }

  @Test
  void paginationIsValidatedAndDeterministic() throws Exception {
    mockMvc
        .perform(authed("/api/erp/v1/parts").param("page", "1").param("page_size", "1"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.items[0].part_id").value("P001"))
        .andExpect(jsonPath("$.data.total").value(5));

    mockMvc
        .perform(authed("/api/erp/v1/parts").param("page", "2").param("page_size", "1"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.items[0].part_id").value("P002"));

    mockMvc
        .perform(authed("/api/erp/v1/parts").param("page", "99").param("page_size", "2"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.items.length()").value(0))
        .andExpect(jsonPath("$.data.total").value(5));
  }

  @Test
  void outOfRangePaginationIsRejected() throws Exception {
    for (String[] bad : new String[][] {{"page", "0"}, {"page", "-1"}, {"page_size", "0"}, {"page_size", "101"}}) {
      mockMvc
          .perform(authed("/api/erp/v1/parts").param(bad[0], bad[1]))
          .andExpect(status().isBadRequest())
          .andExpect(jsonPath("$.error.code").value("INVALID_ARGUMENT"));
    }
  }

  @Test
  void serviceTokenIsRequiredAndVerified() throws Exception {
    mockMvc
        .perform(get("/api/erp/v1/suppliers"))
        .andExpect(status().isUnauthorized())
        .andExpect(jsonPath("$.error.code").value("UNAUTHORIZED"));

    mockMvc
        .perform(get("/api/erp/v1/suppliers").header("X-Service-Token", "wrong-token"))
        .andExpect(status().isUnauthorized());
  }

  @Test
  void healthIsOpenButReadinessReportsTheDatabase() throws Exception {
    mockMvc
        .perform(get("/api/erp/v1/health"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.status").value("ok"));

    mockMvc
        .perform(get("/api/erp/v1/health/ready"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.database").value("up"));
  }

  @Test
  void reRunningTheSeedLoaderDoesNotDuplicateRows() throws Exception {
    // The loader runs on every startup; it must only seed an empty database. Restart persistence
    // of real edits is proven end-to-end by tests/acceptance/test_t02.py.
    seedLoader.run(new DefaultApplicationArguments(new String[0]));

    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM suppliers", Integer.class)).isEqualTo(3);
    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM parts", Integer.class)).isEqualTo(5);
    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM supplier_parts", Integer.class))
        .isEqualTo(8);
    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM inventory", Integer.class)).isEqualTo(5);
  }
}
