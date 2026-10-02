package com.rushharness.erp;

import com.rushharness.erp.config.ErpProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

/**
 * Procurement inventory mini-ERP.
 *
 * <p>T02 provides the catalog and inventory read endpoints backed by a file-based H2 database;
 * ordering arrives in T03.
 */
@SpringBootApplication
@EnableConfigurationProperties(ErpProperties.class)
public class ErpApplication {

  public static void main(String[] args) {
    SpringApplication.run(ErpApplication.class, args);
  }
}
