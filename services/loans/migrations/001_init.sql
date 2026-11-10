CREATE TABLE members (
  id INT IDENTITY(1, 1) PRIMARY KEY,
  name NVARCHAR(200) NOT NULL,
  email NVARCHAR(320) NOT NULL CONSTRAINT members_email_unique UNIQUE
);

-- tool_id points at a tool in the catalog service's database, so it has no
-- foreign key. borrow_tool checks it over HTTP before inserting.
CREATE TABLE loans (
  id INT IDENTITY(1, 1) PRIMARY KEY,
  tool_id INT NOT NULL,
  member_id INT NOT NULL CONSTRAINT loans_member_fk REFERENCES members(id),
  borrowed_at NVARCHAR(32) NOT NULL,
  due_at NVARCHAR(32) NOT NULL,
  returned_at NVARCHAR(32) NULL
);

CREATE UNIQUE INDEX one_open_loan_per_tool
  ON loans(tool_id) WHERE returned_at IS NULL;
