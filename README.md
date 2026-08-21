# WBN CRM

Internal Customer Success metrics portal for Wellbeing Nutrition.

## Data sources

- Zoho Desk: Customer Experience and Customer Escalation, Tickets by Channel
- BIK: Customer Ticket Report and All Conversations Report

Imports run at 06:00, 12:00, and 18:00 IST. The web application reads only from its database; source credentials remain in the server environment and are never committed.

## Production

- Host: existing WBNANALYTICS Azure VM
- Web: Nginx → Gunicorn on `127.0.0.1:5100`
- Data: SQLite WAL database on the VM managed disk, with scheduled backup
- Access: Microsoft Entra SSO restricted to `@wellbeingnutrition.com`
