import unittest
from datetime import date

from app import app
from loyalty_service import _parent_fulfilment_bucket
from management_dashboard_service import sales_management


class ReleaseSanityTests(unittest.TestCase):
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
                session["signed_in_at"] = "2026-08-27T10:00:00+05:30"
            response = client.get("/dashboard")
            html = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertLess(html.index("Sales Dashboard</a>"), html.index("CS Dashboard</a>"))
            self.assertIn('value="sales"', html)

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
