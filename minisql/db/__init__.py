"""Acceso a Oracle. Todo lo de aquí corre en hilos de trabajo, nunca en la interfaz."""
import oracledb

# Si tu Oracle es 11g o anterior, el modo "thin" no funciona: descomenta esto
# y apunta a tu Oracle Instant Client.
# oracledb.init_oracle_client(lib_dir=r"C:\oracle\instantclient_21_13")

oracledb.defaults.fetch_lobs = False  # CLOB llega como texto, BLOB como bytes
