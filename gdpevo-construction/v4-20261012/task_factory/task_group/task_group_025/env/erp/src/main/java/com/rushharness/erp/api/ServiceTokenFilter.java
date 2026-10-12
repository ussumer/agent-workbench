package com.rushharness.erp.api;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.rushharness.erp.config.ErpProperties;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Rejects callers that do not present the internal service token.
 *
 * <p>The ERP trusts a verified service identity instead of the request body. This token is only
 * handed to the MCP gateway on the host: it is never given to the browser, the sandbox or the
 * model. Health probes stay open because they expose no business data.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
public class ServiceTokenFilter extends OncePerRequestFilter {

  private static final String PROTECTED_PREFIX = "/api/erp/v1/";

  private final ErpProperties properties;
  private final ObjectMapper objectMapper;

  public ServiceTokenFilter(ErpProperties properties, ObjectMapper objectMapper) {
    this.properties = properties;
    this.objectMapper = objectMapper;
  }

  @Override
  protected boolean shouldNotFilter(HttpServletRequest request) {
    String path = request.getRequestURI();
    if (!path.startsWith(PROTECTED_PREFIX)) {
      return true; // never protect actuator or static paths
    }
    return path.equals(PROTECTED_PREFIX + "health")
        || path.equals(PROTECTED_PREFIX + "health/ready");
  }

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain chain)
      throws ServletException, IOException {

    String expected = properties.getSecurity().getServiceToken();
    String provided = request.getHeader(properties.getSecurity().getHeaderName());
    if (expected == null || expected.isBlank()) {
      throw new IllegalStateException("erp.security.service-token must be configured");
    }
    if (!constantTimeEquals(expected, provided)) {
      response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
      response.setContentType(MediaType.APPLICATION_JSON_VALUE);
      response.setCharacterEncoding(StandardCharsets.UTF_8.name());
      ApiException failure =
          ApiException.unauthorized(
              "missing or invalid " + properties.getSecurity().getHeaderName() + " header");
      objectMapper.writeValue(
          response.getOutputStream(),
          java.util.Map.of(
              "error",
              java.util.Map.of("code", failure.code(), "message", failure.getMessage(), "details", java.util.Map.of()),
              "request_id",
              RequestContext.currentRequestId()));
      return;
    }
    chain.doFilter(request, response);
  }

  /**
   * Length-independent comparison so a wrong token cannot be recovered byte by byte from timing.
   */
  private static boolean constantTimeEquals(String expected, String provided) {
    if (provided == null) {
      return false;
    }
    return MessageDigest.isEqual(
        expected.getBytes(StandardCharsets.UTF_8), provided.getBytes(StandardCharsets.UTF_8));
  }
}
