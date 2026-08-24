# WBN CRM

Internal Customer Success metrics portal for Wellbeing Nutrition.

## Data sources

- Zoho Desk: Customer Experience and Customer Escalation, Tickets by Channel
- BIK: Customer Ticket Report and All Conversations Report
- Shopify: employee-attributed daily, Monday–Sunday weekly, and monthly sales

Zoho and BIK imports run at 09:00 and 21:00 IST. Shopify imports run hourly. Authorized users can also request a complete refresh from the dashboard. The web application reads only from its database; source credentials remain in Azure Key Vault/the protected server environment and are never committed.

## Production

- Host: existing WBNANALYTICS Azure VM
- Web: Nginx → Gunicorn on `127.0.0.1:5100`
- Data: SQLite WAL database on the VM managed disk, with scheduled backup
- Access: Microsoft Entra SSO restricted to `@wellbeingnutrition.com`
