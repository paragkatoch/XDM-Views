-- =============================================================================
-- Customer Database Schema and Dummy Data
-- =============================================================================

-- Drop existing tables if they exist
DROP TABLE IF EXISTS CustomerLoyalty;
DROP TABLE IF EXISTS Customer;

-- Create Customer table
CREATE TABLE Customer (
    customer_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL
);

-- Create CustomerLoyalty table
CREATE TABLE CustomerLoyalty (
    loyalty_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    membership_tier TEXT NOT NULL,
    reward_points INTEGER NOT NULL,
    enrolled_on TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES Customer(customer_id)
);

-- Insert dummy customer data
INSERT INTO Customer (customer_id, name, city) VALUES
(1, 'Alice Johnson', 'New York'),
(2, 'Bob Smith', 'Los Angeles'),
(3, 'Charlie Brown', 'Chicago'),
(4, 'Diana Prince', 'Houston'),
(5, 'Eve Wilson', 'Phoenix'),
(6, 'Frank Miller', 'Philadelphia'),
(7, 'Grace Lee', 'San Antonio'),
(8, 'Henry Taylor', 'San Diego'),
(9, 'Isaac Newton', 'Dallas'),
(10, 'Julia Roberts', 'San Jose');

-- Insert dummy loyalty data
INSERT INTO CustomerLoyalty (loyalty_id, customer_id, membership_tier, reward_points, enrolled_on) VALUES
(1001, 1, 'Gold', 4200, '2023-01-15'),
(1002, 2, 'Silver', 1850, '2023-03-02'),
(1003, 4, 'Platinum', 7600, '2022-11-09'),
(1004, 5, 'Gold', 3980, '2023-06-21'),
(1005, 7, 'Bronze', 640, '2024-01-11'),
(1006, 8, 'Silver', 2140, '2023-08-04'),
(1007, 10, 'Gold', 4875, '2022-12-30');

-- Verify data
SELECT * FROM Customer;
SELECT * FROM CustomerLoyalty;
