# Phase 4: Product & Warranty Management

Implement **Phase 4 — Product and Warranty Management** for the AssureX claims system.

The goal is to allow users to register products, manage their warranty information, and automatically determine the current product age and warranty status.

## 1. Product Registration

Create a complete product registration workflow with the following fields:

### Required Product Fields

* Product Name
* Brand
* Category
* Model
* Serial Number
* Purchase Date
* Purchase Price
* Retailer
* Warranty Duration

### Requirements

* Product Name, Brand, Category, Model, Serial Number, Purchase Date, Retailer, and Warranty Duration should be validated.
* Serial numbers should be unique where applicable.
* Purchase Price should accept valid monetary values and reject negative values.
* Purchase Date cannot be in the future.
* Warranty Duration should support a clear unit such as months or years.
* Display appropriate validation and error messages.
* Preserve entered form data when validation fails.
* Show a confirmation state after successful registration.

## 2. Warranty Records

Create a warranty record associated with each registered product.

Each warranty record should contain:

* Start Date
* Expiry Date
* Provider
* Coverage
* Exclusions
* Extended Warranty
* Service-Center Conditions

### Warranty Logic

The system should automatically calculate the expiry date from:

`Warranty Start Date + Warranty Duration`

Do not require users to manually calculate or enter the expiry date when it can be derived from the warranty duration.

Support extended warranties by allowing an additional warranty period to be associated with an existing product.

The system should clearly distinguish between:

* Original warranty
* Extended warranty

## 3. Automatic Calculations

The system must automatically calculate and expose these values:

### `product_age`

Calculate the age of the product based on the purchase date and the current date.

Display it in a human-readable format, for example:

* `6 months`
* `1 year, 4 months`
* `3 years`

Avoid displaying negative ages.

### `warranty_remaining`

Calculate the amount of time remaining before the active warranty expires.

Examples:

* `8 months remaining`
* `27 days remaining`
* `Expired`

If the warranty has expired, the remaining value should not be negative.

### `warranty_status`

Automatically determine the warranty status.

Supported statuses:

```text
Active
Near Expiry
Expired
Extended Warranty
```

Use the following logic:

```text
IF extended warranty is currently active
    status = "Extended Warranty"

ELSE IF warranty expiry date has passed
    status = "Expired"

ELSE IF warranty is approaching expiry
    status = "Near Expiry"

ELSE
    status = "Active"
```

Define the "Near Expiry" threshold centrally rather than hard-coding it throughout the application. Use **30 days** as the default threshold.

If an extended warranty exists but has not started yet, continue displaying the applicable current warranty status rather than incorrectly marking it as active.

## 4. Product & Warranty Dashboard

Create a product management interface where users can:

* View all registered products
* Search products
* Filter by category
* Filter by warranty status
* Open a product's details
* Edit product information
* View warranty information
* Add an extended warranty
* View warranty history

Each product should display key information such as:

```text
Product Name
Brand
Model
Serial Number
Purchase Date
Warranty Expiry
Warranty Remaining
Warranty Status
```

Use clear status indicators for:

* Active
* Near Expiry
* Expired
* Extended Warranty

The status should be visually distinct but accessible and should not rely on colour alone.

## 5. Product Details Page

Create a dedicated product details view containing:

### Product Information

* Product name
* Brand
* Category
* Model
* Serial number
* Purchase date
* Price
* Retailer
* Product age

### Warranty Information

* Warranty provider
* Start date
* Expiry date
* Warranty duration
* Warranty remaining
* Warranty status
* Coverage
* Exclusions
* Extended warranty details
* Service-center conditions

### Warranty Timeline

Display the warranty lifecycle visually:

```text
Purchase
   ↓
Warranty Start
   ↓
Current Date
   ↓
Warranty Expiry
   ↓
Extended Warranty (if applicable)
```

Clearly indicate the current position within the warranty period.

## 6. Data Model

Create appropriate database models/entities.

Suggested structure:

### Product

```text
Product
- id
- user_id
- product_name
- brand
- category
- model
- serial_number
- purchase_date
- price
- retailer
- warranty_duration
- warranty_duration_unit
- created_at
- updated_at
```

### Warranty

```text
Warranty
- id
- product_id
- provider
- start_date
- expiry_date
- coverage
- exclusions
- is_extended
- service_center_conditions
- created_at
- updated_at
```

If the existing project already has suitable user/customer/claim relationships, reuse those relationships instead of creating duplicate structures.

Do not unnecessarily modify existing Phase 1–3 functionality.

## 7. API / Backend

Implement the necessary backend functionality for:

```text
POST   /products
GET    /products
GET    /products/:id
PUT    /products/:id
DELETE /products/:id

POST   /products/:id/warranties
GET    /products/:id/warranties
PUT    /warranties/:id
DELETE /warranties/:id
```

Adapt the routes to the project's existing API architecture if different.

The backend should perform validation and calculations rather than relying exclusively on frontend calculations.

Create reusable warranty calculation utilities/services for:

```text
calculateProductAge()
calculateWarrantyExpiry()
calculateWarrantyRemaining()
calculateWarrantyStatus()
```

Use the server's current date/time consistently and handle date boundaries correctly.

## 8. Frontend UX

Create a clean, production-ready interface consistent with the existing AssureX design system.

### Product Registration

Use a structured form with logical sections:

**Product Details**

* Product Name
* Brand
* Category
* Model
* Serial Number

**Purchase Details**

* Purchase Date
* Price
* Retailer

**Warranty**

* Warranty Duration
* Duration Unit

After submission, show the calculated warranty information.

### Product List

Use a table or responsive card layout containing:

| Product | Serial Number | Purchase Date | Warranty | Status |
| ------- | ------------- | ------------- | -------- | ------ |

Include:

* Search
* Filtering
* Sorting
* Pagination if necessary
* Empty state
* Loading state
* Error state

## 9. Extended Warranty

Allow users to add an extended warranty to an existing product.

The form should include:

* Provider
* Start Date
* Duration
* Expiry Date
* Coverage
* Exclusions
* Service-Center Conditions

The system should validate that the extended warranty does not create an invalid timeline.

If the extended warranty starts immediately after the original warranty:

```text
Original Warranty
Purchase ───────────── Expiry
                         ↓
Extended Warranty ────── Expiry
```

If the project supports overlapping warranties, handle the overlap explicitly rather than producing ambiguous status calculations.

## 10. Edge Cases

Handle at least these cases:

* Product purchased today
* Product purchased in the future
* Warranty expires today
* Warranty expires tomorrow
* Warranty expired yesterday
* Warranty with less than 30 days remaining
* Warranty with exactly 30 days remaining
* Already expired warranty with an active extension
* Product with no warranty
* Multiple warranty records
* Extended warranty beginning after the original warranty
* Invalid warranty dates
* Duplicate serial numbers
* Missing required fields

Make date calculations timezone-safe and avoid off-by-one-day errors.

## 11. Testing

Add tests for:

### Product

* Successful product registration
* Required-field validation
* Future purchase-date rejection
* Invalid price rejection
* Duplicate serial-number handling

### Warranty

* Correct expiry calculation
* Active warranty detection
* Near-expiry detection
* Expired warranty detection
* Extended warranty detection
* Warranty remaining calculation
* Product age calculation

### Example

Given:

```text
Purchase Date: 2025-01-01
Warranty Duration: 2 years
Current Date: 2026-09-26
```

The system should calculate approximately:

```text
Product Age: 1 year, 8 months
Warranty Expiry: 2027-01-01
Warranty Remaining: approximately 3 months
Warranty Status: Active
```

## 12. Implementation Rules

Before writing code:

1. Inspect the existing AssureX codebase.
2. Understand the existing architecture, database schema, authentication, API patterns, routing, UI components, and design system.
3. Reuse existing components and utilities wherever possible.
4. Do not introduce a new framework or database technology unnecessarily.
5. Do not break existing Phase 1–3 functionality.
6. Follow the project's existing naming conventions and folder structure.
7. Keep calculations centralized and reusable.
8. Add appropriate database migrations.
9. Add validation on both frontend and backend.
10. Add automated tests for the new functionality.

## 13. Definition of Done

Phase 4 is complete when a user can:

1. Register a product.
2. Enter its purchase and warranty information.
3. Automatically receive the calculated warranty expiry date.
4. Automatically see product age.
5. Automatically see warranty time remaining.
6. Automatically receive the correct warranty status.
7. View all registered products.
8. Search and filter products.
9. Open a product's complete details.
10. Edit product information.
11. Add an extended warranty.
12. View warranty history.
13. See correct status changes as time passes.
14. Successfully use all functionality without affecting existing AssureX features.

Implement this as a **complete production-ready Phase 4 feature**, not merely a UI mockup. After implementation, run the existing test suite and the new Phase 4 tests, fix any failures, and provide a concise summary of the files changed, database migrations added, API endpoints created, and tests completed.
