import tempfile, unittest
from pathlib import Path
from app import create_app, db, User, Complaint

class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_app({"TESTING":True,"WTF_CSRF_ENABLED":False,
            "SQLALCHEMY_DATABASE_URI":"sqlite:///"+str(Path(self.tmp.name)/"test.sqlite"),
            "SECRET_KEY":"test-key","MODEL_PATH":str(Path(self.tmp.name)/"missing.joblib")})
        self.client=self.app.test_client()
        with self.app.app_context():
            db.create_all()
            u=User(name="Test Student",email="student@example.com",role="student");u.set_password("password123")
            a=User(name="Admin",email="admin@example.com",role="admin");a.set_password("adminpassword123")
            h=User(name="Hostel Admin",email="hostel@example.com",role="department_admin",department="Hostel");h.set_password("hostelpassword123")
            db.session.add_all([u,a,h]);db.session.flush()
            c1=Complaint(tracking_id="CD-HOSTEL01",user_id=u.id,complaint_text="Hostel tap broken",category="Hostel",priority="High")
            c2=Complaint(tracking_id="CD-LIBRARY1",user_id=u.id,complaint_text="Library hours issue",category="Library",priority="Normal")
            db.session.add_all([c1,c2]);db.session.commit()
    def tearDown(self):
        with self.app.app_context(): db.session.remove(); db.drop_all()
        self.tmp.cleanup()
    def login(self,email,password):
        return self.client.post("/login",data={"email":email,"password":password},follow_redirects=True)
    def test_home(self):
        self.assertEqual(self.client.get("/").status_code,200)
    def test_student_login(self):
        r=self.login("student@example.com","password123")
        self.assertEqual(r.status_code,200)
    def test_student_cannot_access_admin(self):
        self.login("student@example.com","password123")
        self.assertEqual(self.client.get("/admin").status_code,403)
    def test_department_admin_sees_only_assigned_category(self):
        self.login("hostel@example.com","hostelpassword123")
        response=self.client.get("/admin")
        self.assertEqual(response.status_code,200)
        self.assertIn(b"CD-HOSTEL01",response.data)
        self.assertNotIn(b"CD-LIBRARY1",response.data)
    def test_department_admin_cannot_open_other_department_complaint(self):
        self.login("hostel@example.com","hostelpassword123")
        self.assertEqual(self.client.get("/complaints/CD-LIBRARY1").status_code,403)
    def test_department_admin_cannot_update_other_department(self):
        self.login("hostel@example.com","hostelpassword123")
        response=self.client.post("/admin/complaints/2/update",
            data={"status":"Resolved","note":"Should be forbidden"})
        self.assertEqual(response.status_code,403)
        self.login("hostel@example.com","hostelpassword123")
        self.assertEqual(self.client.get("/complaints/CD-LIBRARY1").status_code,403)
    def test_health_endpoint(self):
        self.assertEqual(self.client.get("/health").status_code,200)
    def test_password_is_hashed(self):
        with self.app.app_context():
            u=User.query.filter_by(email="student@example.com").first()
            self.assertNotEqual(u.password_hash,"password123")
if __name__=="__main__": unittest.main()
