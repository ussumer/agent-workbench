package com.rushharness.erp;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

@SpringBootTest
@AutoConfigureMockMvc
class SourcingApiTest {
  @DynamicPropertySource
  static void configure(DynamicPropertyRegistry r) {
    r.add("spring.datasource.url", () -> "jdbc:h2:file:./target/test-db/sourcing-" + UUID.randomUUID());
    r.add("erp.security.service-token", () -> "test-sourcing-token");
    r.add("sourcing.seed-path", () -> "../seed.json");
  }
  @Autowired MockMvc mvc;
  @Autowired JdbcTemplate jdbc;
  @Test void targetProgramsExistAndPaginationIsStable() throws Exception {
    mvc.perform(get("/api/sourcing/programs").header("X-Service-Token", "test-sourcing-token").param("offset","1").param("limit","2"))
      .andExpect(status().isOk()).andExpect(jsonPath("$.data.total").value(150))
      .andExpect(jsonPath("$.data.items[0].program_id").value("PRG-002"));
    for (String id : new String[]{"021","034","047","058","069","083","096","107","118","129"})
      mvc.perform(get("/api/sourcing/programs/PRG-"+id).header("X-Service-Token", "test-sourcing-token"))
        .andExpect(status().isOk()).andExpect(jsonPath("$.data.variants.length()").value(2));
  }
  @Test void candidatesForecastVersionsAndDocumentsAreReachable() throws Exception {
    mvc.perform(get("/api/sourcing/quotes").header("X-Service-Token", "test-sourcing-token").param("program_id","PRG-021"))
      .andExpect(status().isOk()).andExpect(jsonPath("$.data.total").value(12));
    mvc.perform(get("/api/sourcing/forecasts").header("X-Service-Token", "test-sourcing-token").param("program_id","PRG-021"))
      .andExpect(status().isOk()).andExpect(jsonPath("$.data.total").value(16));
    mvc.perform(get("/api/sourcing/documents/Q-021-0-base-r2").header("X-Service-Token", "test-sourcing-token"))
      .andExpect(status().isOk()).andExpect(jsonPath("$.data.record.currency").exists())
      .andExpect(jsonPath("$.data.record.approval_status").value("approved"));
  }
  @Test @Transactional void repliesFollowDatabaseEdits() throws Exception {
    jdbc.update("UPDATE sourcing_records SET payload = ? WHERE record_id = ?", "{\"program_id\":\"PRG-021\",\"name\":\"Database authority\"}", "PRG-021");
    mvc.perform(get("/api/sourcing/programs/PRG-021").header("X-Service-Token", "test-sourcing-token"))
      .andExpect(jsonPath("$.data.name").value("Database authority"));
  }
  @Test void filtersHaveCountsAndInvalidPaginationFails() throws Exception {
    mvc.perform(get("/api/sourcing/quotes").header("X-Service-Token", "test-sourcing-token").param("program_id","PRG-021").param("supplier_id","SUP-010"))
      .andExpect(status().isOk()).andExpect(jsonPath("$.data.total").value(4));
    mvc.perform(get("/api/sourcing/programs").header("X-Service-Token", "test-sourcing-token").param("limit","0"))
      .andExpect(status().isBadRequest());
    mvc.perform(get("/api/sourcing/programs/PRG-999").header("X-Service-Token", "test-sourcing-token"))
      .andExpect(status().isNotFound());
  }
  @Test void sourcingRequiresTokenAndWriteRoutesAreClosed() throws Exception {
    mvc.perform(get("/api/sourcing/programs")).andExpect(status().isUnauthorized());
    mvc.perform(post("/api/sourcing/programs").header("X-Service-Token", "test-sourcing-token")).andExpect(status().isForbidden());
    mvc.perform(post("/api/erp/v1/orders").header("X-Service-Token", "test-sourcing-token").contentType("application/json").content("{}"))
      .andExpect(status().isForbidden());
    mvc.perform(get("/api/judge").header("X-Service-Token", "test-sourcing-token")).andExpect(status().isNotFound());
  }
  @Test void commercialHistoryHasSubstantiveBranches() throws Exception {
    mvc.perform(get("/api/sourcing/quotes").header("X-Service-Token", "test-sourcing-token").param("program_id","PRG-021").param("supplier_id","SUP-012"))
      .andExpect(jsonPath("$.data.items[2].approval_status").value("approved"))
      .andExpect(jsonPath("$.data.items[3].approval_status").value("revoked"));
    mvc.perform(get("/api/sourcing/contracts").header("X-Service-Token", "test-sourcing-token").param("program_id","PRG-021"))
      .andExpect(jsonPath("$.data.total").value(0));
    mvc.perform(get("/api/sourcing/contracts").header("X-Service-Token", "test-sourcing-token").param("program_id","PRG-047"))
      .andExpect(jsonPath("$.data.total").value(2));
    mvc.perform(get("/api/sourcing/documents/C-047-nomination").header("X-Service-Token", "test-sourcing-token"))
      .andExpect(jsonPath("$.data.record.cash_refund_fraction_of_settled_upfront").value("0.30"));
    mvc.perform(get("/api/sourcing/documents/POL-021").header("X-Service-Token", "test-sourcing-token"))
      .andExpect(jsonPath("$.data.record.first_discounted_fiscal_period").value(2028))
      .andExpect(jsonPath("$.data.record.tooling_amortization").doesNotExist())
      .andExpect(jsonPath("$.data.record.source_convention").doesNotExist());
    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM sourcing_records WHERE domain='quotes' AND payload LIKE '%\"approval_status\":\"draft\"%'",Integer.class)).isPositive();
    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM sourcing_records WHERE domain='quotes' AND payload LIKE '%\"tier_application\":\"flat\"%'",Integer.class)).isPositive();
  }
  @Test @Transactional void healthRejectsUncommittedSeed() throws Exception {
    jdbc.update("DELETE FROM sourcing_seed");
    mvc.perform(get("/health")).andExpect(status().isServiceUnavailable());
  }
  @Test void healthChecksDatabaseAndRecordsContainNoComputedAnswers() throws Exception {
    mvc.perform(get("/health")).andExpect(status().isOk()).andExpect(jsonPath("$.data.database").value("up"));
    for (String forbidden : new String[]{"winner", "expected_npv", "rubric", "task_id"})
      assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM sourcing_records WHERE payload LIKE ?", Integer.class, "%\""+forbidden+"\"%" )).isZero();
    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM sourcing_records WHERE domain='programs'", Integer.class)).isEqualTo(150);
  }
}


