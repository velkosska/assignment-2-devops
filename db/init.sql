-- Local only: one database and one login per service. Run by the db-init
-- compose service on every `docker compose up`, so every step is idempotent.
-- Passwords arrive as sqlcmd variables from .env and must not contain a quote.
SET NOCOUNT ON;
GO

IF DB_ID('catalog') IS NULL CREATE DATABASE catalog;
GO
IF DB_ID('loans') IS NULL CREATE DATABASE loans;
GO

IF SUSER_ID('catalog_user') IS NULL
    CREATE LOGIN catalog_user WITH PASSWORD = N'$(CATALOG_PWD)', DEFAULT_DATABASE = catalog;
ELSE
    ALTER LOGIN catalog_user WITH PASSWORD = N'$(CATALOG_PWD)';
GO
IF SUSER_ID('loans_user') IS NULL
    CREATE LOGIN loans_user WITH PASSWORD = N'$(LOANS_PWD)', DEFAULT_DATABASE = loans;
ELSE
    ALTER LOGIN loans_user WITH PASSWORD = N'$(LOANS_PWD)';
GO

-- Each login gets a user only in its own database, so it cannot open the other one.
USE catalog;
GO
IF USER_ID('catalog_user') IS NULL CREATE USER catalog_user FOR LOGIN catalog_user;
ALTER ROLE db_owner ADD MEMBER catalog_user;
GO

USE loans;
GO
IF USER_ID('loans_user') IS NULL CREATE USER loans_user FOR LOGIN loans_user;
ALTER ROLE db_owner ADD MEMBER loans_user;
GO

PRINT 'db-init: databases catalog and loans and their logins are ready';
GO
