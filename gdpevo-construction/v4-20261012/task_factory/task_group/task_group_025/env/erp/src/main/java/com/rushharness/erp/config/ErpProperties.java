package com.rushharness.erp.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** Typed access to the {@code erp.*} configuration block. */
@ConfigurationProperties(prefix = "erp")
public class ErpProperties {

  private final Seed seed = new Seed();
  private final Security security = new Security();

  public Seed getSeed() {
    return seed;
  }

  public Security getSecurity() {
    return security;
  }

  public static class Seed {

    private String path = "../fixtures/seed-v1.json";
    private String fallbackPath = "fixtures/seed-v1.json";

    public String getPath() {
      return path;
    }

    public void setPath(String path) {
      this.path = path;
    }

    public String getFallbackPath() {
      return fallbackPath;
    }

    public void setFallbackPath(String fallbackPath) {
      this.fallbackPath = fallbackPath;
    }
  }

  public static class Security {

    private String serviceToken;
    private String headerName = "X-Service-Token";

    public String getServiceToken() {
      return serviceToken;
    }

    public void setServiceToken(String serviceToken) {
      this.serviceToken = serviceToken;
    }

    public String getHeaderName() {
      return headerName;
    }

    public void setHeaderName(String headerName) {
      this.headerName = headerName;
    }
  }
}
