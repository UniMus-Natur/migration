---
layout: default
title: NIRD Application
nav_order: 6
---

# NIRD Resource Application

This document contains the project description and summary used for the NIRD resource application.

## Project Description (Summary)

The UniMus-Natur project is migrating the Natural History Museum's legacy dataset (MUSIT) to the Specify 7 platform. This transition is vital for securing millions of natural history records and maintaining their availability for international biodiversity research.

We request NIRD resources to host a fully containerized staging environment orchestrated via Kubernetes. This validated environment consists of a live Specify 7 web instance, worker nodes, and MariaDB databases running as Docker microservices.

Our methodology involves an ETL pipeline where data is extracted from the legacy Oracle system and loaded into this NIRD-hosted architecture using Python scripts and the Specify Django ORM. Hosting the live Specify staging instance on NIRD is critical; it enables us to verify the migration results through the actual user interface and API in a controlled Kubernetes environment. The requested resources will drive this containerized stack and support the computationally intensive, iterative processes required to transform, clean, and validate the complete dataset before production release.

## Relevant Usage Experience

Our team has extensive specialized experience with NIRD resources through the **GBIF Norway** project (**NS8095K**), where we manage scalable services using **Kubernetes** and **Minio** object storage.

We are proficient in container orchestration and cloud-native infrastructure management. This expertise guarantees efficient utilization of the requested resources and rapid deployment of the containerized Specify 7 staging environment.

## Measurable Output

The primary output is the secured continuity and enhanced accessibility of over 15 million natural history records, which serve as the empirical foundation for numerous Master's and PhD theses at the University of Oslo annually. The museum's data generated **621 peer-reviewed publications in 2025 alone** (tracked by GBIF), a high-volume output we expect to maintain or exceed by modernizing to the Specify 7 platform. This project ensures continued global exploitation and dissemination through direct integration with the Norwegian Species Map Service (Artskart) and GBIF, safeguarding the data's compatibility with international standards for future research, conservation, and policy-making.

## Storage Request Justification (2 TiB)

We request **2 TiB** of project storage on NIRD to support the migration of the MUSIT database and potential future attachment of media assets.

**Estimation Basis:**
1.  **Database Size**: The core legacy Oracle database (15+ million records) and its corresponding MariaDB staging version require significant space for raw data, indexes, and temporary transaction logs generated during the ETL process. We estimate approximately **500 GiB** for the active database footprint including overhead for multiple schema versions during testing.
2.  **Backup & Snapshots**: To ensure data safety during destructive transformation tests, we require capacity for daily snapshots and point-in-time recovery dumps, estimated at **1 TiB** (retaining multiple full backups).
3.  **Future Proofing (Images/Media)**: The remaining capacity (**~500 GiB**) is reserved for partially staging high-resolution specimen images or attachment files that may need to be linked to the new Specify 7 instance as the project expands scope.

This 2 TiB allocation provides a safe buffer for the data-intensive migration operations without risking storage exhaustion during critical validation phases.

## Related Documents

*   [**Submitted Proposal (PDF)**](documents/NIRD-application.pdf)
*   [**Data Management Plan (DOCX)**](documents/data-management-plan.docx)
