# Security, performance & quality passes



## 🔒 Security Analysis

### SQL Injection Prevention
```sql
-- ❌ CRITICAL: SQL Injection vulnerability
query = "SELECT * FROM users WHERE id = " + userInput;
query = f"DELETE FROM orders WHERE user_id = {user_id}";

-- ✅ SECURE: Parameterized queries
-- PostgreSQL/MySQL
PREPARE stmt FROM 'SELECT * FROM users WHERE id = ?';
EXECUTE stmt USING @user_id;

-- SQL Server
EXEC sp_executesql N'SELECT * FROM users WHERE id = @id', N'@id INT', @id = @user_id;
```

### Access Control & Permissions
- **Principle of Least Privilege**: Grant minimum required permissions
- **Role-Based Access**: Use database roles instead of direct user permissions
- **Schema Security**: Proper schema ownership and access controls
- **Function/Procedure Security**: Review DEFINER vs INVOKER rights

### Data Protection
- **Sensitive Data Exposure**: Avoid SELECT * on tables with sensitive columns
- **Audit Logging**: Ensure sensitive operations are logged
- **Data Masking**: Use views or functions to mask sensitive data
- **Encryption**: Verify encrypted storage for sensitive data

## ⚡ Performance Optimization

### Query Structure Analysis
```sql
-- ❌ BAD: Inefficient query patterns
SELECT DISTINCT u.* 
FROM users u, orders o, products p
WHERE u.id = o.user_id 
AND o.product_id = p.id
AND YEAR(o.order_date) = 2024;

-- ✅ GOOD: Optimized structure
SELECT u.id, u.name, u.email
FROM users u
INNER JOIN orders o ON u.id = o.user_id
WHERE o.order_date >= '2024-01-01' 
AND o.order_date < '2025-01-01';
```

### Index Strategy Review
- **Missing Indexes**: Identify columns that need indexing
- **Over-Indexing**: Find unused or redundant indexes
- **Composite Indexes**: Multi-column indexes for complex queries
- **Index Maintenance**: Check for fragmented or outdated indexes

### Join Optimization
- **Join Types**: Verify appropriate join types (INNER vs LEFT vs EXISTS)
- **Join Order**: Optimize for smaller result sets first
- **Cartesian Products**: Identify and fix missing join conditions
- **Subquery vs JOIN**: Choose the most efficient approach

### Aggregate and Window Functions
```sql
-- ❌ BAD: Inefficient aggregation
SELECT user_id, 
       (SELECT COUNT(*) FROM orders o2 WHERE o2.user_id = o1.user_id) as order_count
FROM orders o1
GROUP BY user_id;

-- ✅ GOOD: Efficient aggregation
SELECT user_id, COUNT(*) as order_count
FROM orders
GROUP BY user_id;
```

## 🛠️ Code Quality & Maintainability

### SQL Style & Formatting
```sql
-- ❌ BAD: Poor formatting and style
select u.id,u.name,o.total from users u left join orders o on u.id=o.user_id where u.status='active' and o.order_date>='2024-01-01';

-- ✅ GOOD: Clean, readable formatting
SELECT u.id,
       u.name,
       o.total
FROM users u
LEFT JOIN orders o ON u.id = o.user_id
WHERE u.status = 'active'
  AND o.order_date >= '2024-01-01';
```

### Naming Conventions
- **Consistent Naming**: Tables, columns, constraints follow consistent patterns
- **Descriptive Names**: Clear, meaningful names for database objects
- **Reserved Words**: Avoid using database reserved words as identifiers
- **Case Sensitivity**: Consistent case usage across schema

### Schema Design Review
- **Normalization**: Appropriate normalization level (avoid over/under-normalization)
- **Data Types**: Optimal data type choices for storage and performance
- **Constraints**: Proper use of PRIMARY KEY, FOREIGN KEY, CHECK, NOT NULL
- **Default Values**: Appropriate default values for columns
