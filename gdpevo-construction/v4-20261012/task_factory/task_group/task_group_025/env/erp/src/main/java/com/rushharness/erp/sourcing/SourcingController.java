package com.rushharness.erp.sourcing;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.rushharness.erp.api.ApiException;
import com.rushharness.erp.api.ApiResponse;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.*;

@RestController
public class SourcingController {
  private static final Set<String> DOMAINS=Set.of("programs","suppliers","forecasts","quotes","engineering","contracts","payments");
  private final JdbcTemplate jdbc;
  private final ObjectMapper mapper;
  public SourcingController(JdbcTemplate jdbc,ObjectMapper mapper){this.jdbc=jdbc;this.mapper=mapper;}
  private JsonNode parse(String payload){try{return mapper.readTree(payload);}catch(Exception e){throw new IllegalStateException("Invalid stored business record",e);}}
  @GetMapping("/api/sourcing/{domain}")
  public ApiResponse<Map<String,Object>> list(@PathVariable String domain,
      @RequestParam(defaultValue="") String query,
      @RequestParam(name="program_id",required=false) String program,
      @RequestParam(name="supplier_id",required=false) String supplier,
      @RequestParam(defaultValue="0") int offset,@RequestParam(defaultValue="50") int limit){
    if(!DOMAINS.contains(domain))throw ApiException.notFound("DOMAIN_NOT_FOUND","Unknown business domain");
    if(offset<0||limit<1||limit>100)throw ApiException.invalidArgument("INVALID_ARGUMENT","offset>=0 and 1<=limit<=100 required",Map.of());
    String where=" WHERE domain=?";
    List<Object> args=new ArrayList<>();args.add(domain);
    if(program!=null){where+=" AND program_id=?";args.add(program);}
    if(supplier!=null){where+=" AND supplier_id=?";args.add(supplier);}
    if(!query.isBlank()){where+=" AND (LOWER(record_id) LIKE ? OR LOWER(payload) LIKE ?)";String q="%"+query.toLowerCase(Locale.ROOT).replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%";args.add(q);args.add(q);}
    long total=jdbc.queryForObject("SELECT COUNT(*) FROM sourcing_records"+where,Long.class,args.toArray());
    args.add(limit);args.add(offset);
    List<JsonNode> items=jdbc.query("SELECT payload FROM sourcing_records"+where+" ORDER BY record_id LIMIT ? OFFSET ?",(r,n)->parse(r.getString(1)),args.toArray());
    return ApiResponse.ok(Map.of("items",items,"total",total,"offset",offset,"limit",limit));
  }
  @GetMapping("/api/sourcing/{domain}/{id}")
  public ApiResponse<JsonNode> detail(@PathVariable String domain,@PathVariable String id){
    if(!Set.of("programs","documents").contains(domain))throw ApiException.notFound("DOMAIN_NOT_FOUND","No detail route for domain");
    var rows=jdbc.query("SELECT payload FROM sourcing_records WHERE domain=? AND record_id=?",(r,n)->parse(r.getString(1)),domain,id);
    if(rows.isEmpty())throw ApiException.notFound("RECORD_NOT_FOUND","Business record does not exist");
    return ApiResponse.ok(rows.getFirst());
  }
  @GetMapping("/health")
  public ApiResponse<Map<String,Object>> health(){
    jdbc.queryForObject("SELECT 1",Integer.class);
    if(jdbc.queryForObject("SELECT COUNT(*) FROM sourcing_seed",Integer.class)!=1)
      throw ApiException.databaseUnavailable("Business seed transaction is not ready");
    return ApiResponse.ok(Map.of("status","ready","database","up","service","java-sourcing-erp","state_mode","read_only"));
  }
}

