package com.rushharness.erp.inventory;

import com.rushharness.erp.api.PageResponse;
import com.rushharness.erp.api.Pagination;
import java.util.ArrayList;
import java.util.List;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

/**
 * Inventory warnings.
 *
 * <p>The predicate is strictly {@code on_hand < warning_threshold}. The suggested quantity only
 * exists for rows that satisfy that predicate — a part can be below its target stock without being
 * warned, and such a part must not appear here.
 */
@Repository
public class InventoryRepository {

  private static final String WARNING_PREDICATE = " WHERE i.on_hand < i.warning_threshold";

  private static final RowMapper<InventoryWarning> WARNING_MAPPER =
      (rs, rowNum) -> {
        int onHand = rs.getInt("on_hand");
        int target = rs.getInt("target_stock");
        return new InventoryWarning(
            rs.getString("part_id"),
            rs.getString("sku"),
            rs.getString("name"),
            onHand,
            rs.getInt("warning_threshold"),
            target,
            Math.max(target - onHand, 0));
      };

  private final JdbcTemplate jdbc;

  public InventoryRepository(JdbcTemplate jdbc) {
    this.jdbc = jdbc;
  }

  public PageResponse<InventoryWarning> findWarnings(Pagination pagination) {
    Long total =
        jdbc.queryForObject(
            "SELECT COUNT(*) FROM inventory i" + WARNING_PREDICATE, Long.class);

    List<Object> params = new ArrayList<>();
    params.add(pagination.pageSize());
    params.add(pagination.offset());
    List<InventoryWarning> items =
        jdbc.query(
            "SELECT i.part_id, p.sku, p.name, i.on_hand, i.warning_threshold, i.target_stock"
                + " FROM inventory i"
                + " JOIN parts p ON p.part_id = i.part_id"
                + WARNING_PREDICATE
                + " ORDER BY i.part_id ASC LIMIT ? OFFSET ?",
            WARNING_MAPPER,
            params.toArray());
    return PageResponse.of(items, total == null ? 0L : total, pagination);
  }
}
