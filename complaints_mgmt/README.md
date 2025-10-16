# Complaints Management System

**Version:** 18.0.1.0.0
**Category:** Services
**License:** LGPL-3

## Overview

The Complaints Management module provides a complete workflow for handling user complaints against companies. It features a three-tier role system with secure record access rules, activity-based notifications, and a structured approval process.

## Features

### Core Functionality
- **User Complaint Submission**: Users can submit complaints against companies with detailed descriptions and supporting documents
- **Child Contact Support**: Users can submit complaints on behalf of their child contacts
- **Multi-Currency Support**: Handle monetary amounts in different currencies
- **Document Attachments**: Upload and manage supporting documents throughout the complaint lifecycle

### Workflow States
1. **New**: Initial complaint submission
2. **In Progress**: Administrator assigns to company for review
3. **Approved**: Company approves the complaint
4. **In Payment**: Administrator initiates payment process
5. **PAID**: Final state - payment completed (terminal)
6. **Rejected**: Company rejects the complaint (terminal)

### Role-Based Access Control

#### Complaint User
- Submit complaints
- View own complaints and complaints for their children
- Upload additional documents when requested

#### Complaint Company User
- View complaints targeting their company
- Request additional documents from complainants
- Approve or reject complaints
- Add company notes and specify covered amounts

#### Complaint Administrator
- View all complaints
- Move complaints through workflow stages
- Set payment states
- Full CRUD access to all records

### Security
- **Record Rules**: Enforce visibility based on role
  - Users see only their own complaints
  - Company users see only complaints targeting their company
  - Admins see all complaints
- **Access Control Lists**: Role-based CRUD permissions
- **Field-Level Security**: Sensitive fields hidden from unauthorized users

### Notifications & Activities
- Automatic activity creation when:
  - Complaint moves to "In Progress" (assigned to company reviewer)
  - Company requests additional documents (assigned to complainant)
- Chatter integration for all state transitions
- Message notifications to complainants and admins

## Installation

### Prerequisites
- Odoo 18.0
- Python 3.10+
- Dependencies: `base`, `mail` modules

### Install Steps

1. **Copy module to addons path:**
   ```bash
   cp -r complaints_mgmt /opt/odoo18/
   ```

2. **Update addons path in odoo.conf** (if not already included):
   ```ini
   addons_path = /opt/odoo18/odoo/addons,...,/opt/odoo18/complaints_mgmt
   ```

3. **Restart Odoo service:**
   ```bash
   sudo systemctl restart odoo18
   ```

4. **Activate developer mode** in Odoo web interface:
   - Settings → Activate Developer Mode

5. **Update Apps List:**
   - Apps → Update Apps List

6. **Install the module:**
   - Search for "Complaints Management"
   - Click Install

### Command-Line Installation
```bash
# Activate virtual environment
source /opt/odoo18/odoo-venv/bin/activate

# Install module
python3 /opt/odoo18/odoo/odoo-bin -c /opt/odoo18/odoo.conf -d DATABASE_NAME -i complaints_mgmt --stop-after-init
```

## Configuration

### Setup Groups
After installation, assign users to appropriate groups:

1. Navigate to: **Settings → Users & Companies → Users**
2. Edit user and go to **Access Rights** tab
3. Assign to group under **Services** category:
   - **Complaint User**: Regular users who submit complaints
   - **Complaint Company User**: Company reviewers (includes User rights)
   - **Complaint Administrator**: System admins (includes User rights)

### Configure Company Users
- Company users must have their `company_id` set to the company they will review complaints for
- Complaints targeting that company will be visible to them

## Usage

### As a User (Complainant)

1. **Create a Complaint:**
   - Go to **Complaints → All Complaints**
   - Click **Create**
   - Fill in:
     - Target Company
     - Description
     - Amount Requested
     - Optionally select a child contact
   - Upload supporting documents
   - Save

2. **Track Status:**
   - View complaint in list/kanban view
   - Status colors: Red (Rejected), Green (Paid), Yellow (In Progress), Blue (Approved)

3. **Respond to Document Requests:**
   - If company requests documents, you'll receive an activity notification
   - Open the complaint and upload additional files in the Attachments tab

### As a Company User (Reviewer)

1. **Review Assigned Complaints:**
   - Go to **Complaints → All Complaints**
   - You'll see complaints targeting your company
   - When admin sets status to "In Progress", you'll receive an activity

2. **Take Action:**
   - Open complaint form
   - Review description and attachments
   - Add company notes
   - Specify amount your company will cover
   - Choose action:
     - **Request Documents**: Ask for more supporting files
     - **APPROVE**: Accept the complaint (moves to Approved)
     - **REJECT**: Deny the complaint (terminal state)

### As an Administrator

1. **Assign for Review:**
   - Open new complaint
   - Click **Set In Progress**
   - Company user will be notified

2. **Manage Payments:**
   - After company approves, set **Amount Approved by Admin**
   - Click **In Payment** to start payment process
   - Once paid, click **Mark Paid** (terminal state)

3. **Monitor All Complaints:**
   - Use filters and grouping in list view
   - Access all fields and notes

## Technical Details

### Models

#### `complaints.complaint`
Main model inheriting from `mail.thread` and `mail.activity.mixin`

**Key Fields:**
- `name`: Complaint reference (auto-generated: COMP00001)
- `partner_id`: Complainant (readonly, defaults to current user)
- `child_partner_id`: Optional child contact
- `company_target_id`: Target company
- `state`: Workflow state
- `amount_requested`, `amount_covered_company`, `amount_approved_admin`: Monetary fields
- `attachment_ids`: M2M to ir.attachment

**Methods:**
- `action_set_in_progress()`: Admin action
- `action_set_in_payment()`: Admin action
- `action_set_paid()`: Admin action
- `action_request_docs()`: Company action
- `action_approve()`: Company action
- `action_reject()`: Company action

#### `res.company.complaint`
Configuration model linking companies to complaint handling

### Views
- **List View**: With state-based decorations
- **Kanban View**: Grouped by state
- **Form View**: With statusbar and role-gated buttons
- **Search View**: Filters by state, grouping options

### Security Files
- `security/groups.xml`: Three security groups
- `security/ir.model.access.csv`: ACL rules
- `security/rules.xml`: Record rules for data visibility

## Testing

Run tests with:
```bash
python3 /opt/odoo18/odoo/odoo-bin -c /opt/odoo18/odoo.conf -d TEST_DB -u complaints_mgmt --test-enable --stop-after-init
```

**Test Coverage:**
- Complete workflow (new → paid)
- Rejection workflow
- Document request workflow
- Child partner constraint validation
- Security: user sees own only
- Permission checks for admin actions

## Uninstallation

1. **Via UI:**
   - Apps → Complaints Management → Uninstall

2. **Via Command Line:**
   ```bash
   # This will remove module data but preserve historical records if needed
   # Backup database before uninstalling!
   ```

## Support & Contributing

- **Author**: AlshayebCo
- **Website**: https://www.alshayebco.com
- **Issues**: Report bugs or request features via your support channel

## Changelog

### Version 18.0.1.0.0 (2025-10-15)
- Initial release
- Complete workflow implementation
- Three-tier role system
- Activity-based notifications
- Multi-currency support
- Comprehensive test coverage

## License

LGPL-3. See LICENSE file for full copyright and licensing details.
