package com.rushharness.erp;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Procurement inventory mini-ERP. Business endpoints arrive from T02 onward; T00 only proves the
 * project builds and serves a minimal health check.
 */
@SpringBootApplication
public class ErpApplication {

  public static void main(String[] args) {
    SpringApplication.run(ErpApplication.class, args);
  }
}
