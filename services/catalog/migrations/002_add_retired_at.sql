-- Remember when a tool was retired. Nullable, so every existing row stays valid and no
-- code that reads tools breaks. The tools already retired get created_at as the best date
-- we know: the real date was never stored.
ALTER TABLE tools ADD retired_at NVARCHAR(32) NULL;
GO

-- A new batch: SQL Server only knows the column after the ALTER above has run.
UPDATE tools SET retired_at = created_at WHERE retired = 1 AND retired_at IS NULL;
