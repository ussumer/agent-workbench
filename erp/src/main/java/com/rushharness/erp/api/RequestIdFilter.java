package com.rushharness.erp.api;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.UUID;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Establishes the request id (and the optional actor id) for the whole request, so both success
 * and error envelopes can echo it. Runs before the token filter so that rejected callers still get
 * a correlation id back.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class RequestIdFilter extends OncePerRequestFilter {

  public static final String REQUEST_ID_HEADER = "X-Request-Id";
  public static final String ACTOR_HEADER = "X-Actor-Id";

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain chain)
      throws ServletException, IOException {

    String requestId = request.getHeader(REQUEST_ID_HEADER);
    if (requestId == null || requestId.isBlank()) {
      requestId = "req-" + UUID.randomUUID();
    }
    String actorId = request.getHeader(ACTOR_HEADER);
    if (actorId != null && actorId.isBlank()) {
      actorId = null;
    }

    RequestContext.begin(requestId, actorId);
    response.setHeader(REQUEST_ID_HEADER, requestId);
    try {
      chain.doFilter(request, response);
    } finally {
      RequestContext.clear();
    }
  }
}
