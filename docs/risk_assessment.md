---
layout: default
title: Risk Assessment
nav_order: 5
---

# Risk Assessment: Database Strategy

## Introduction

This document assesses the risks associated with the different database organization strategies proposed for the UniMus:Natur migration to Specify. The assessment focuses on five main options ranging from a completely centralized model to a highly fragmented one.

## Risk Evaluation Matrix

Risks are evaluated based on **Likelihood** (Low, Medium, High) and **Impact** (Low, Medium, High).

| Risk Level | Description |
| :--- | :--- |
| **High** | Critical issue that could halt operations or significantly degrade data quality/usability. Requires mitigation. |
| **Medium** | Significant issue that complicates workflows or administration. Monitoring required. |
| **Low** | Minor issue with acceptable trade-offs. |

## Detailed Analysis of Options

### Option 1: Single Database (All Museums, All Collections)

**Description**: One Specify instance containing data for all 60+ collections across all museums.

| Risk Category | Risk Description | Likelihood | Impact | Overall Level | Mitigation |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Operational** | **"All or Nothing" Restore**: A data corruption event in one collection requires rolling back the entire database, affecting all other museums/collections. | Low | High | **High** | - Point-in-time recovery strategies.<br>- Strict access controls to prevent accidental mass-updates. |
| **Performance** | Performance degradation due to table size (millions of records) and concurrent user load. | Medium | Medium | **Medium** | - Database indexing optimization.<br>- Hardware scaling. |
| **Governance** | Governance conflicts: Museums disagreeing on shared standards (e.g., Geography tree, agents). | High | Medium | **High** | - Strong central governance committee with decision-making power. |
| **Security** | Complex permission schemes required to isolate sensitive data between museums. | High | High | **High** | - Granular Role-Based Access Control (RBAC). |

### Option 2: One Database per Museum (5 Databases)

**Description**: Separate databases for each museum (Oslo, Bergen, Trondheim, Tromsø, Stavanger).

| Risk Category | Risk Description | Likelihood | Impact | Overall Level | Mitigation |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Data Integrity** | **Cross-Museum Standardization**: Creating a national standard becomes harder as each museum manages its own lists (e.g., Geography). | Medium | Medium | **Medium** | - National coordination group.<br>- Shared configuration templates. |
| **User Experience** | **Siloed Access**: Researchers needing access to collections in multiple museums need separate accounts or logins (mitigated by FEIDE/SSO). | Low | Low | **Low** | - SSO (Single Sign-On). |
| **Collaboration** | **Shared Resources**: Cannot share "Person" or "agents" across museums, leading to duplicate agent records for the same collector nationally. | High | Low | **Medium** | - National Agent Registry service (external). |

### Option 3: One Database per Main Organism Group (4 Databases)

**Description**: Databases organized by discipline (e.g., Botany, Zoology) shared across all museums.

| Risk Category | Risk Description | Likelihood | Impact | Overall Level | Mitigation |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Governance** | **Complex Governance**: Museums must agree on standards within a discipline. A decision in "Botany" affects all 5 museums. | High | Medium | **High** | - Strong discipline-specific councils. |
| **Data Integrity** | **Cross-Discipline Silos**: Cannot link resources between disciplines (e.g., a host plant in Botany linked to an insect in Entomology). | Medium | Medium | **Medium** | - External linking via persistent identifiers (UUIDs). |

### Option 4: One Database per Main Organism Group per Museum (20 Databases)

**Description**: Databases organized by discipline AND museum (e.g., Oslo Botany, Bergen Botany, etc.).

| Risk Category | Risk Description | Likelihood | Impact | Overall Level | Mitigation |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Operational** | **High Fragmentation**: Managing 20+ databases increases upgrade and maintenance complexity significantly compared to 1-5 DBs. | High | Medium | **High** | - Automation (Ansible). |
| **User Experience** | **Fragmented Experience**: A user in Oslo working on both Botany and Zoology needs two different environments. | High | Low | **Medium** | - SSO. |
| **Data Integrity** | **Maximum Duplication**: Agents, Geography, and Taxonomy references are duplicated across 20 silos. Synchronization is very difficult. | High | High | **High** | - Aggressive external authority management. |

### Option 5: One Database per Collection (~60 Databases)

**Description**: Maximum isolation, treating each collection as a separate Specify instance.

| Risk Category | Risk Description | Likelihood | Impact | Overall Level | Mitigation |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Operational** | **Administration Overhead**: Updating schemas, managing users, and backing up 60+ separate databases is labor-intensive. | High | High | **High** | - Infrastructure-as-Code (Ansible/Terraform) for automation.<br>- Centralized user management. |
| **Data Functionality** | **Loss of Cross-Collection Queries**: Cannot easily query "All samples collected by Person X" across the institution. | High | High | **High** | - Build a separate "Data Warehouse" or discovery portal for aggregate queries. |
| **Standardization** | **Schema Drift**: Over time, individual collections diverge in field usage, making future aggregation impossible. | High | High | **High** | - Enforce strict template management policies. |

## Summary recommendation

From a **Risk Management** perspective:

*   **Option 1 (Single DB)** carries high governance and "blast radius" risks (one error affects everyone), but offers the best data integrity and lowest admin overhead.
*   **Option 5 (Many DBs)** carries extreme operational and data fragmentation risks, requiring significant investment in automation and external tooling to mitigate.

**Conclusion**: If resources for automation and building external aggregation tools are low, **Option 1 or 2 (fewer databases)** presents the most manageable risk profile despite the governance challenges.
