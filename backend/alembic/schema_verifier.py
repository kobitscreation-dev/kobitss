"""
Schema Verifier & Adoption Engine for Kobits Alembic Migrations.

Provides safe, deterministic verification and migration of database schemas:
1. Fresh Database: Creates all tables, columns, constraints, foreign keys, and indexes.
2. Existing Pre-Alembic Database:
   - Compares expected declarative schema (Base.metadata) with actual database schema.
   - Creates missing tables cleanly.
   - Adds missing columns to existing tables using op.add_column without data loss.
   - Verifies column type compatibility.
   - Creates missing indexes.
   - Aborts cleanly with descriptive RuntimeError if schema is fundamentally incompatible.
3. Fully Compatible Database: Adopts existing schema without data loss or table recreation.
"""

from typing import Tuple, List, Dict, Any, Set
from sqlalchemy import inspect, Column
from alembic.operations import Operations


def check_type_compatibility(expected_type: Any, existing_type: Any) -> Tuple[bool, str]:
    exp_str = str(expected_type).upper()
    ext_str = str(existing_type).upper()
    
    # 1. String / Text / Enum / JSON / CLOB / VARCHAR
    string_types = ("VARCHAR", "TEXT", "STRING", "CHAR", "ENUM", "JSON", "CLOB")
    if any(s in exp_str for s in string_types):
        if any(s in ext_str for s in string_types):
            return True, "compatible string/text/enum/json type"
        return False, f"expected string/text type ({exp_str}), but existing column type is {ext_str}"
        
    # 2. Integer / SmallInteger / BigInteger / Boolean
    int_types = ("INT", "INTEGER", "SMALLINT", "BIGINT", "TINYINT", "BOOL", "BOOLEAN")
    if any(i in exp_str for i in int_types):
        if any(i in ext_str for i in int_types):
            return True, "compatible integer/boolean type"
        return False, f"expected integer/boolean type ({exp_str}), but existing column type is {ext_str}"
        
    # 3. Float / Real / Numeric / Decimal
    float_types = ("FLOAT", "REAL", "NUMERIC", "DECIMAL", "DOUBLE")
    if any(f in exp_str for f in float_types):
        if any(f in ext_str for f in float_types):
            return True, "compatible float/numeric type"
        return False, f"expected float/numeric type ({exp_str}), but existing column type is {ext_str}"
        
    # 4. DateTime / Date / Timestamp
    date_types = ("DATE", "DATETIME", "TIMESTAMP", "TIME")
    if any(d in exp_str for d in date_types):
        if any(d in ext_str for d in date_types) or "VARCHAR" in ext_str or "TEXT" in ext_str:
            return True, "compatible datetime/timestamp type"
        return False, f"expected datetime/timestamp type ({exp_str}), but existing column type is {ext_str}"
        
    if exp_str in ext_str or ext_str in exp_str:
        return True, "exact or substring type match"
        
    return False, f"incompatible types: expected {exp_str}, got {ext_str}"


def verify_and_migrate_schema(op: Operations, bind: Any, metadata: Any) -> Dict[str, Any]:
    """
    Verifies and migrates the database schema safely against SQLAlchemy metadata.
    Returns a summary dict detailing actions taken.
    """
    inspector = inspect(bind)
    existing_tables: Set[str] = set(inspector.get_table_names())
    
    summary: Dict[str, Any] = {
        "tables_created": [],
        "columns_added": [],
        "indexes_created": [],
        "tables_verified": []
    }

    # Iterate through metadata.sorted_tables in topological foreign-key order
    for table in metadata.sorted_tables:
        table_name = table.name
        
        if table_name not in existing_tables:
            # 1. Table missing -> Create table cleanly
            table.create(bind)
            summary["tables_created"].append(table_name)
        else:
            # 2. Table exists -> Verify columns and add missing ones
            summary["tables_verified"].append(table_name)
            existing_cols_list = inspector.get_columns(table_name)
            existing_cols: Dict[str, Any] = {c["name"]: c for c in existing_cols_list}
            
            for col in table.columns:
                col_name = col.name
                if col_name not in existing_cols:
                    # Determine server_default if adding NOT NULL column
                    serv_def = col.server_default
                    nullable = col.nullable
                    
                    if not nullable and serv_def is None:
                        if col.default is not None and getattr(col.default, "arg", None) is not None:
                            def_val = col.default.arg
                            if isinstance(def_val, str):
                                serv_def = f"'{def_val}'"
                            else:
                                serv_def = str(def_val)
                        else:
                            col_type_str = str(col.type).upper()
                            if any(t in col_type_str for t in ("INT", "BOOL", "FLOAT", "NUMERIC")):
                                serv_def = "0"
                            elif any(t in col_type_str for t in ("CHAR", "TEXT", "STRING", "VARCHAR", "ENUM")):
                                serv_def = "''"
                            else:
                                nullable = True

                    col_to_add = Column(col_name, col.type, nullable=nullable, server_default=serv_def)
                    op.add_column(table_name, col_to_add)
                    summary["columns_added"].append(f"{table_name}.{col_name}")
                else:
                    # Column exists -> Check type compatibility
                    existing_info = existing_cols[col_name]
                    is_compat, reason = check_type_compatibility(col.type, existing_info["type"])
                    if not is_compat:
                        raise RuntimeError(
                            f"Incompatible database schema detected in table '{table_name}', "
                            f"column '{col_name}': {reason}"
                        )

            # 3. Verify indexes on existing table
            existing_indexes: Set[str] = {idx["name"] for idx in inspector.get_indexes(table_name) if idx.get("name")}
            for index in table.indexes:
                if index.name and index.name not in existing_indexes:
                    index.create(bind)
                    summary["indexes_created"].append(index.name)

    return summary
