import unittest
from datetime import date, datetime
from unittest.mock import patch

from app import app
from loyalty_service import _customer_rows, _fulfilment, _parent_fulfilment_bucket
from management_dashboard_service import sales_management
from shopify_service import _date_chunks
from sales_celebration_mailer import ranking_email
from sales_recognition_scheduler import run as run_recognition


class ReleaseSanityTests(unittest.TestCase):
    def test_loyalty_identity_matching_is_transitive(self):
        source_rows = [
            {"day": "2026-08-01", "customer_id": "1", "customer_name": "VARUN", "customer_email": "v@gmail.com", "order_count": 1},
            {"day": "2026-08-02", "customer_id": "2", "customer_name": "VARUN", "customer_email": "v@gmail.com", "order_count": 1},
            {"day": "2026-08-03", "customer_id": "3", "customer_name": "VARUN", "customer_email": "v1@gmail.com", "order_count": 1},
        ]
        order_rows = [
            {"customer_name": "VARUN", "customer_email": "v@gmail.com", "customer_phone": "123"},
            {"customer_name": "VARUN", "customer_email": "v@gmail.com", "customer_phone": "345"},
            {"customer_name": "VARUN", "customer_email": "v1@gmail.com", "customer_phone": "123"},
        ]
        customers = _customer_rows(source_rows, order_rows)
        self.assertEqual(len(customers), 1)
        self.assertEqual(customers[0]["order_count"], 3)
        self.assertEqual(customers[0]["tier"], "Repeat")

    @patch("sales_celebration_mailer._period_rankings")
    def test_monday_month_to_date_email_has_no_yesterday_section(self, rankings):
        rankings.return_value = [{"employee_key": "a", "name": "Agent", "role": "Sales", "sales": 100, "orders": 1}]
        message = ranking_email("mtd", date(2026, 8, 1), date(2026, 8, 30))
        self.assertIn("Month-to-Date Sales Report", message["subject"])
        self.assertIn("Month-to-date sales", message["body"])
        self.assertNotIn("Yesterday’s sales", message["body"])

    @patch("sales_recognition_scheduler._init_log")
    @patch("sales_recognition_scheduler.send_ranking")
    def test_monday_uses_mtd_and_tuesday_uses_yesterday(self, send_ranking, _init_log):
        run_recognition("daily", datetime(2026, 8, 31, 11, 0))
        send_ranking.assert_called_once_with("mtd", date(2026, 8, 1), date(2026, 8, 30))
        send_ranking.reset_mock()
        run_recognition("daily", datetime(2026, 9, 1, 11, 0))
        send_ranking.assert_called_once_with("daily", date(2026, 8, 31), date(2026, 8, 31))

    def test_shopify_sync_uses_bounded_date_chunks(self):
        chunks = list(_date_chunks(date(2026, 7, 17), date(2026, 8, 30)))
        self.assertEqual(chunks[0], (date(2026, 7, 17), date(2026, 7, 23)))
        self.assertEqual(chunks[-1][1], date(2026, 8, 30))
        self.assertTrue(all((end - start).days <= 6 for start, end in chunks))

    @patch("loyalty_service.uniware_items_for_orders", return_value=[])
    @patch("loyalty_service.uniware_orders_between")
    @patch("loyalty_service.shopify_orders_between")
    @patch("loyalty_service.uniware_order_coverage")
    def test_awaiting_uniware_only_counts_orders_inside_coverage(
        self, coverage, shopify_orders, uniware_orders, _items
    ):
        coverage.return_value = {"start_date": "2026-08-01", "end_date": "2026-08-31"}
        shopify_orders.return_value = [
            {"order_id": "old", "local_date": "2026-01-10", "role": "Unassigned", "display_name": "Unassigned"},
            {"order_id": "matched", "local_date": "2026-08-10", "role": "Unassigned", "display_name": "Unassigned"},
            {"order_id": "waiting", "local_date": "2026-08-11", "role": "Unassigned", "display_name": "Unassigned"},
        ]
        uniware_orders.return_value = [{
            "linked_shopify_order_id": "matched", "sale_order_code": "SO-1",
            "fulfilment_bucket": "Delivered", "item_status_summary": {"Delivered": 1},
            "item_count": 1, "role": "Unassigned", "display_name": "Unassigned",
        }]
        result = _fulfilment("overall", "2026-01-01", "2026-12-31")
        self.assertEqual(result["reconciliation"]["shopify_orders"], 3)
        self.assertEqual(result["reconciliation"]["shopify_orders_in_coverage"], 2)
        self.assertEqual(result["reconciliation"]["shopify_missing_uniware"], 1)

    def test_parent_order_outcome_precedence(self):
        self.assertEqual(_parent_fulfilment_bucket([{"fulfilment_bucket": "Delivered"}]), "Delivered")
        self.assertEqual(_parent_fulfilment_bucket([{"fulfilment_bucket": "Cancelled"}]), "Cancelled")
        self.assertEqual(_parent_fulfilment_bucket([
            {"fulfilment_bucket": "Delivered"}, {"fulfilment_bucket": "Returned"}
        ]), "Returned")
        self.assertEqual(_parent_fulfilment_bucket([
            {"fulfilment_bucket": "Delivered"}, {"fulfilment_bucket": "Pending"}
        ]), "Pending")

    def test_discount_uses_signed_net_and_shopify_gross_denominator(self):
        row = {
            "employee_key": "agent", "display_name": "Agent", "role": "Sales",
            "total": 900.0, "gross_sales": 1000.0, "discounts": -100.0, "orders": 2,
            "values": [900.0], "gross_values": [1000.0], "discount_values": [-100.0],
        }
        reversal = {
            "employee_key": "returns", "display_name": "Returns", "role": "Unassigned",
            "total": -45.0, "gross_sales": -50.0, "discounts": 5.0, "orders": 0,
            "values": [-45.0], "gross_values": [-50.0], "discount_values": [5.0],
        }
        report = {"rows": [row, reversal], "product_rows": [], "columns": [{"label": "Day"}]}
        result = sales_management(report, "overall")
        self.assertEqual(result["discounts"], 95.0)
        self.assertAlmostEqual(result["discount_rate"], 10.0)

    def test_standard_period_filters_and_default_dashboard_order(self):
        with app.test_client() as client:
            with client.session_transaction() as session:
                session["user"] = {
                    "email": "varun.c@wellbeingnutrition.com", "name": "Varun",
                    "role": "admin", "permissions": [],
                }
                session["signed_in_at"] = datetime.now().astimezone().isoformat()
            response = client.get("/dashboard")
            html = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn("Sales Dashboard</a>", html)
            self.assertNotIn("CS Dashboard</a>", html)
            self.assertNotIn("Attendance</a>", html)

            weekly = client.get("/shopify/orders?period=weekly&week_end=2026-08-27")
            weekly_html = weekly.get_data(as_text=True)
            self.assertEqual(weekly.status_code, 200)
            self.assertIn('name="week_end" value="2026-08-28"', weekly_html)
            self.assertNotIn("Last 90 days", weekly_html)

            empty = client.get("/dashboard/sales/details?period=yearly&year=2020&q=definitely-no-result")
            self.assertEqual(empty.status_code, 200)
            self.assertIn("app.js", empty.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
