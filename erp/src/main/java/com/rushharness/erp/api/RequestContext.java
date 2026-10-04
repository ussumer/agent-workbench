package com.rushharness.erp.api;

/**
 * Request-scoped correlation and identity.
 *
 * <p>The actor id is taken from the trusted service header, never from a request body. It is
 * stored in a ThreadLocal only for the duration of one request and cleared in a finally block, so
 * no mutable "current user" ever leaks between requests.
 */
public final class RequestContext {

  private static final ThreadLocal<String> REQUEST_ID = new ThreadLocal<>();
  private static final ThreadLocal<String> ACTOR_ID = new ThreadLocal<>();

  private RequestContext() {}

  public static void begin(String requestId, String actorId) {
    REQUEST_ID.set(requestId);
    ACTOR_ID.set(actorId);
  }

  public static void clear() {
    REQUEST_ID.remove();
    ACTOR_ID.remove();
  }

  public static String currentRequestId() {
    String value = REQUEST_ID.get();
    return value == null ? "req-unknown" : value;
  }

  /** @return the caller's actor id, or {@code null} when the caller did not supply one */
  public static String currentActorId() {
    return ACTOR_ID.get();
  }
}
