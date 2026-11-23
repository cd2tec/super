-- Migracao de mentira: abre o banco e cria o livro de ordem.
PRAGMA foreign_keys = ON;
CREATE TABLE ORDEM (ID INTEGER PRIMARY KEY AUTOINCREMENT, ARQUIVO TEXT NOT NULL);
INSERT INTO ORDEM (ARQUIVO) VALUES ('beforeMigrate');
