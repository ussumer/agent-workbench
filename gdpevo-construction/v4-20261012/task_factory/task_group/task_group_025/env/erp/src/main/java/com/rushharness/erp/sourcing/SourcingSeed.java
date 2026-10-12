package com.rushharness.erp.sourcing;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.transaction.PlatformTransactionManager;

@Component
public class SourcingSeed implements ApplicationRunner {
  private final JdbcTemplate jdbc;
  private final ObjectMapper mapper;
  private final TransactionTemplate tx;
  private final String path;
  public SourcingSeed(JdbcTemplate jdbc, ObjectMapper mapper, PlatformTransactionManager manager,
      @Value("${sourcing.seed-path:../seed.json}") String path) {
    this.jdbc=jdbc; this.mapper=mapper; this.tx=new TransactionTemplate(manager); this.path=path;
  }
  @Override public void run(ApplicationArguments args) throws Exception {
    byte[] bytes=Files.readAllBytes(Path.of(path));
    String hash=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    JsonNode seed=mapper.readTree(bytes);
    long seedId=seed.path("seed").asLong();
    tx.executeWithoutResult(s -> {
      var loaded=jdbc.queryForList("SELECT sha256 FROM sourcing_seed WHERE seed_id=?",String.class,seedId);
      if (!loaded.isEmpty()) {
        if (!loaded.getFirst().equals(hash)) throw new IllegalStateException("Seed changed; operator must use a new dedicated database");
        return;
      }
      if (jdbc.queryForObject("SELECT COUNT(*) FROM sourcing_records",Integer.class)>0)
        throw new IllegalStateException("Existing sourcing data has no matching seed receipt");
      for(JsonNode row: seed.path("records"))
        jdbc.update("INSERT INTO sourcing_records(domain,record_id,program_id,supplier_id,payload) VALUES(?,?,?,?,?)",
          row.path("domain").asText(),row.path("record_id").asText(),row.path("program_id").isNull()?null:row.path("program_id").asText(),row.path("supplier_id").isNull()?null:row.path("supplier_id").asText(),row.path("payload").toString());
      jdbc.update("INSERT INTO sourcing_seed(seed_id,sha256) VALUES(?,?)",seedId,hash);
    });
  }
}
