import json
import re
import os
from html.parser import HTMLParser

# Configuration
INPUT_FILE = "docs/documents/Felter i Karplanter.html"
OUTPUT_JSON = "docs/_data/musit_fields.json"
OUTPUT_MERMAID = "docs/_includes/musit_erd.mermaid"

def clean_text(text):
    if not text:
        return ""
    # Remove zero-width spaces and excess whitespace
    text = text.replace('\u200b', '').strip()
    return " ".join(text.split())

class DocParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sections = []
        self.current_section = None
        self.in_table = False
        self.current_table = None
        self.in_tr = False
        self.current_row = []
        self.in_td = False
        self.current_cell_text = []
        self.in_header = False
        self.current_header_text = []
        self.capture_header = False
        self.header_level = 0
        self.id_map = {}

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        
        if tag in ['h1', 'h2', 'h3', 'h4']:
            self.capture_header = True
            self.header_level = int(tag[1])
            self.current_header_text = []
            # Check for anchor with id inside or just use empty
            # Often the ID is on an 'a' tag inside the h tag
        
        elif tag == 'a' and self.capture_header:
            if 'id' in attr_dict:
                 self.current_header_id = attr_dict['id']

        elif tag == 'table':
            self.in_table = True
            self.current_table = {"headers": [], "rows": []}
            
        elif tag == 'tr':
            self.in_tr = True
            self.current_row = []
            
        elif tag in ['td', 'th']:
            self.in_td = True
            self.current_cell_text = []

    def handle_endtag(self, tag):
        if tag in ['h1', 'h2', 'h3', 'h4']:
            self.capture_header = False
            title = clean_text("".join(self.current_header_text))
            self.current_section = {
                "title": title,
                "level": self.header_level,
                "tables": [],
                "id": getattr(self, 'current_header_id', None)
            }
            self.sections.append(self.current_section)
            self.current_header_id = None
            
        elif tag == 'table':
            self.in_table = False
            if self.current_section and self.current_table['rows'] or self.current_table['headers']:
                 self.current_section["tables"].append(self.current_table)
            self.current_table = None
            
        elif tag == 'tr':
            self.in_tr = False
            if self.current_table is not None:
                # Assume first row is header if empty
                if not self.current_table['headers']:
                    self.current_table['headers'] = self.current_row
                else:
                    # Zip headers with row
                    if len(self.current_row) > 0:
                        row_dict = {}
                        for i, cell in enumerate(self.current_row):
                            if i < len(self.current_table['headers']):
                                row_dict[self.current_table['headers'][i]] = cell
                        self.current_table['rows'].append(row_dict)
        
        elif tag in ['td', 'th']:
            self.in_td = False
            text = clean_text("".join(self.current_cell_text))
            self.current_row.append(text)

    def handle_data(self, data):
        if self.capture_header:
            self.current_header_text.append(data)
        elif self.in_td:
            self.current_cell_text.append(data)

def normalize_table_name(name):
    """Normalize table names for Mermaid (no spaces, special chars)"""
    if not name or name.lower() in ["tabell", "table"]:
        return None
    # Extract just the table name if it looks like "Table: NAME"
    # Based on docs content like "HIERARCHICAL_PLACE"
    return re.sub(r'[^a-zA-Z0-9_]', '_', name).upper()

def infer_relationships_and_entities(sections):
    entities = {}
    relationships = []
    
    for section in sections:
        for table_data in section['tables']:
            headers = [h.lower() for h in table_data['headers']]
            
            try:
                table_col_idx = -1
                field_col_idx = -1
                comment_col_idx = -1
                
                for idx, h in enumerate(headers):
                    if "tabell" in h and "felt" not in h: # "Tabell"
                        table_col_idx = idx
                    elif "feltnavn" in h: # "Feltnavn i tabell"
                        field_col_idx = idx
                    elif "kommentar" in h:
                        comment_col_idx = idx
                
                if table_col_idx != -1 and field_col_idx != -1:
                    header_names = table_data['headers']
                    table_key = header_names[table_col_idx]
                    field_key = header_names[field_col_idx]
                    comment_key = header_names[comment_col_idx] if comment_col_idx != -1 else None

                    for row in table_data['rows']:
                        table_name = normalize_table_name(row.get(table_key))
                        field_name = row.get(field_key)
                        comment = row.get(comment_key, "") if comment_key else ""
                        
                        if table_name and field_name:
                            if table_name not in entities:
                                entities[table_name] = {"fields": []}
                            
                            is_pk = "primary key" in comment.lower()
                            is_fk = "foreign key" in comment.lower()
                            
                            # Deduplicate fields
                            exists = False
                            for f in entities[table_name]["fields"]:
                                if f["name"] == field_name:
                                    exists = True
                                    break
                            if not exists:
                                field_def = {
                                    "name": field_name,
                                    "comment": comment,
                                    "is_pk": is_pk,
                                    "is_fk": is_fk
                                }
                                entities[table_name]["fields"].append(field_def)
                            
                            if is_fk:
                                match = re.search(r'(?:foreign key|fk).*?(?:til|to)\s+([a-zA-Z0-9_]+)', comment, re.IGNORECASE)
                                if match:
                                    target = normalize_table_name(match.group(1))
                                    if target:
                                        relationships.append((table_name, target))
            except Exception as e:
                print(f"Skipping a table due to parse error: {e}")
                continue

    return entities, relationships

def generate_mermaid(entities, relationships):
    lines = ["erDiagram"]
    
    for table_name, data in entities.items():
        if not table_name: continue
        lines.append(f"  {table_name} {{")
        for field in data["fields"]:
            key_token = "PK" if field["is_pk"] else ("FK" if field["is_fk"] else "")
            field_clean = re.sub(r'[^a-zA-Z0-9_]', '', field['name'])
            if not field_clean: continue
            
            # Simple workaround for spaces in comments or no comments
            comment_clean = f'"{field["comment"][:40]}..."' if field["comment"] else ""
            lines.append(f"    string {field_clean} {key_token} {comment_clean}")
        lines.append("  }")
    
    added_rels = set()
    for source, target in relationships:
        if source in entities and target in entities and source != target:
            rel_id = f"{source}-{target}"
            if rel_id not in added_rels:
                lines.append(f"  {source} }}|..|| {target} : references")
                added_rels.add(rel_id)
                lines.append(f"  {source} {{ }}") # Hack to ensure linkage render if needed? No, built-in should work
                
    return "\n".join(lines)

def main():
    if not os.path.exists(INPUT_FILE):
        print(f"Error: {INPUT_FILE} not found.")
        return

    print("Parsing HTML...")
    parser = DocParser()
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        parser.feed(f.read())
    
    sections = parser.sections
    
    print("Inferring Schema...")
    entities, relationships = infer_relationships_and_entities(sections)
    
    print("Generating Mermaid ERD...")
    mermaid_content = generate_mermaid(entities, relationships)
    
    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    os.makedirs(os.path.dirname(OUTPUT_MERMAID), exist_ok=True)
    
    print(f"Writing JSON to {OUTPUT_JSON}...")
    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(sections, f, indent=2, ensure_ascii=False)
        
    print(f"Writing Mermaid to {OUTPUT_MERMAID}...")
    with open(OUTPUT_MERMAID, 'w', encoding='utf-8') as f:
        f.write(mermaid_content)
        
    print("Done.")

if __name__ == "__main__":
    main()
