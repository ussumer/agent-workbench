package com.rushharness.erp.config;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * Loads the single fixed seed ({@code fixtures/seed-v1.json}) into an empty database.
 *
 * <p>Two rules matter here:
 *
 * <ul>
 *   <li>Seeding happens <em>only</em> when the database is empty. Startup never truncates or
 *       overwrites data, so restart persistence is meaningful.
 *   <li>The seed is read from the shared fixture file. There is no hardcoded fallback array: if the
 *       file cannot be found or parsed, the service fails to start instead of quietly serving
 *       invented data.
 * </ul>
 */
@Component
public class SeedLoader implements ApplicationRunner {

  private static final Logger log = LoggerFactory.getLogger(SeedLoader.class);
  private static final String SEED_FILE_NAME = "seed-v1.json";

  private final JdbcTemplate jdbc;
  private final ObjectMapper objectMapper;
  private final ErpProperties properties;

  public SeedLoader(JdbcTemplate jdbc, ObjectMapper objectMapper, ErpProperties properties) {
    this.jdbc = jdbc;
    this.objectMapper = objectMapper;
    this.properties = properties;
  }

  @Override
  public void run(ApplicationArguments args) throws IOException {
    Long existing = jdbc.queryForObject("SELECT COUNT(*) FROM suppliers", Long.class);
    if (existing != null && existing > 0) {
      log.info("seed skipped: database already holds {} supplier row(s)", existing);
      return;
    }

    Path seedPath = resolveSeedPath();
    JsonNode seed = objectMapper.readTree(Files.readString(seedPath));
    String seedId = seed.path("seed_id").asText("<unknown>");
    int inserted = seed(seed);
    log.info("seeded {} rows from {} (seed_id={})", inserted, seedPath, seedId);
  }

  /** Locate the shared seed file without falling back to embedded data. */
  Path resolveSeedPath() {
    List<Path> candidates = new ArrayList<>();
    Path configured = Path.of(properties.getSeed().getPath());
    if (configured.isAbsolute()) {
      candidates.add(configured);
    } else {
      candidates.add(Path.of("").toAbsolutePath().resolve(configured));
      candidates.add(Path.of("").toAbsolutePath().resolve(properties.getSeed().getFallbackPath()));
    }
    // Walk up from the working directory: the service may be started from erp/ or the repo root.
    Path cursor = Path.of("").toAbsolutePath();
    for (int depth = 0; depth < 4 && cursor != null; depth++) {
      candidates.add(cursor.resolve("fixtures").resolve(SEED_FILE_NAME));
      cursor = cursor.getParent();
    }

    for (Path candidate : candidates) {
      Path normalised = candidate.normalize();
      if (Files.isRegularFile(normalised)) {
        return normalised;
      }
    }
    throw new IllegalStateException(
        "seed file not found; looked at " + candidates.stream().map(Path::toString).toList());
  }

  @Transactional
  int seed(JsonNode seed) {
    int rows = 0;
    for (JsonNode supplier : seed.withArray("suppliers")) {
      jdbc.update(
          "INSERT INTO suppliers (supplier_id, name, active, contact) VALUES (?, ?, ?, ?)",
          supplier.get("supplier_id").asText(),
          supplier.get("name").asText(),
          supplier.get("active").asBoolean(),
          supplier.get("contact").asText());
      rows++;
    }
    for (JsonNode part : seed.withArray("parts")) {
      jdbc.update(
          "INSERT INTO parts (part_id, sku, name, unit, active) VALUES (?, ?, ?, ?, ?)",
          part.get("part_id").asText(),
          part.get("sku").asText(),
          part.get("name").asText(),
          part.get("unit").asText(),
          part.get("active").asBoolean());
      rows++;
    }
    for (JsonNode relation : seed.withArray("supplier_parts")) {
      jdbc.update(
          "INSERT INTO supplier_parts (supplier_id, part_id, catalog_price, currency, lead_days)"
              + " VALUES (?, ?, ?, ?, ?)",
          relation.get("supplier_id").asText(),
          relation.get("part_id").asText(),
          new BigDecimal(relation.get("catalog_price").asText()),
          relation.get("currency").asText(),
          relation.get("lead_days").asInt());
      rows++;
    }
    for (JsonNode stock : seed.withArray("inventory")) {
      jdbc.update(
          "INSERT INTO inventory (part_id, on_hand, warning_threshold, target_stock)"
              + " VALUES (?, ?, ?, ?)",
          stock.get("part_id").asText(),
          stock.get("on_hand").asInt(),
          stock.get("warning_threshold").asInt(),
          stock.get("target_stock").asInt());
      rows++;
    }
    return rows;
  }
}
