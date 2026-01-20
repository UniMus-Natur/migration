---
layout: default
title: Database Fields
nav_order: 3
mermaid: true
---

# Database Fields & Schema

This page describes the database schema for the legacy MUSIT Oracle database. It is auto-generated from the original documentation [Felter i Karplanter.html](documents/Felter%20i%20Karplanter.html).

## Entity Relationship Diagram

The following diagram is automatically inferred from the documentation descriptions.

```mermaid
{% include musit_erd.mermaid %}
```

## Field Descriptions

{% assign sections = site.data.musit_fields %}

{% for section in sections %}
<h{{ section.level }} id="{{ section.id }}">{{ section.title }}</h{{ section.level }}>

{% for table in section.tables %}
{% if table.rows.size > 0 %}
<div class="table-wrapper">
<table>
  <thead>
    <tr>
      {% for header in table.headers %}
      <th>{{ header }}</th>
      {% endfor %}
    </tr>
  </thead>
  <tbody>
    {% for row in table.rows %}
    <tr>
      {% for header in table.headers %}
      <td>{{ row[header] }}</td>
      {% endfor %}
    </tr>
    {% endfor %}
  </tbody>
</table>
</div>
{% endif %}
{% endfor %}

{% endfor %}
