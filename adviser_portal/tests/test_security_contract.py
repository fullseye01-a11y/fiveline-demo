import unittest
from pathlib import Path

SQL = (Path(__file__).resolve().parents[1] / "schema.sql").read_text("utf-8")

class TestRlsDesignContract(unittest.TestCase):
    TABLES = ("organizations","members","clients","client_positions",
              "client_risk_assessments","client_allocations","audit_log")

    def test_all_customer_tables_force_rls(self):
        for table in self.TABLES:
            with self.subTest(table=table):
                self.assertIn("ALTER TABLE adviser_private."+table+" ENABLE ROW LEVEL SECURITY",SQL)
                self.assertIn("ALTER TABLE adviser_private."+table+" FORCE ROW LEVEL SECURITY",SQL)

    def test_clients_read_but_cannot_delete_by_default(self):
        self.assertIn("CREATE POLICY clients_read",SQL)
        self.assertIn("CREATE POLICY clients_add",SQL)
        self.assertIn("CREATE POLICY clients_modify",SQL)
        self.assertNotIn("FOR DELETE",SQL)
        self.assertIn("owner_member_id=adviser_private.request_member()",SQL)
        self.assertIn("advisor_member_id=adviser_private.request_member()",SQL)

    def test_child_record_access_checks_parent_client(self):
        for table in ("client_positions","client_risk_assessments","client_allocations"):
            self.assertIn("FROM adviser_private.clients c", SQL)
            self.assertIn("c.org_id="+table+".org_id AND c.id="+table+".client_id", SQL)

    def test_unverified_runtime_is_disabled(self):
        server=(Path(__file__).resolve().parents[1] / "server.py").read_text("utf-8")
        self.assertIn("login_available",server)
        self.assertIn('"client_records": False',server)
        self.assertIn('path.startswith("/api/auth")',server)

if __name__=="__main__":
    unittest.main()
