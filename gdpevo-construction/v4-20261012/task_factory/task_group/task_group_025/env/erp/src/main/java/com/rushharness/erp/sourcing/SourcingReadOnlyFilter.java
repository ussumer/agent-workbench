package com.rushharness.erp.sourcing;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.rushharness.erp.config.ErpProperties;
import jakarta.servlet.*;
import jakarta.servlet.http.*;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
@Order(Ordered.HIGHEST_PRECEDENCE+5)
public class SourcingReadOnlyFilter extends OncePerRequestFilter {
  private final ErpProperties properties;
  private final ObjectMapper mapper;
  public SourcingReadOnlyFilter(ErpProperties properties,ObjectMapper mapper){this.properties=properties;this.mapper=mapper;}
  @Override protected void doFilterInternal(HttpServletRequest req,HttpServletResponse res,FilterChain chain) throws ServletException,IOException {
    String path=req.getRequestURI();
    if(path.startsWith("/api/")){
      String token=req.getHeader("X-Service-Token");
      String expected=properties.getSecurity().getServiceToken();
      if(expected==null||expected.isBlank())throw new IllegalStateException("Service token is required");
      if(token==null||!MessageDigest.isEqual(token.getBytes(StandardCharsets.UTF_8),expected.getBytes(StandardCharsets.UTF_8))){failure(res,401,"UNAUTHORIZED");return;}
      if(!req.getMethod().equals("GET")){failure(res,403,"READ_ONLY");return;}
      if(!path.startsWith("/api/sourcing/")){failure(res,404,"ROUTE_NOT_AVAILABLE");return;}
    }
    chain.doFilter(req,res);
  }
  private void failure(HttpServletResponse res,int status,String code)throws IOException{
    res.setStatus(status);res.setContentType("application/json");mapper.writeValue(res.getOutputStream(),Map.of("error",Map.of("code",code,"message",code)));
  }
}
