# Department administrator upgrade

## Roles
- `student`: submits and tracks their own grievances.
- `department_admin`: manages only the assigned category.
- `admin`: global administrator with institution-wide access.

## Create a department admin (PowerShell)
```powershell
$env:ADMIN_NAME="Hostel Department Admin"
$env:ADMIN_EMAIL="hostel-admin@your-college.edu"
$env:ADMIN_PASSWORD="Use-A-Different-Long-Password"
$env:ADMIN_DEPARTMENT="Hostel"
flask --app app create-department-admin
```

Allowed department values: `Academics`, `Canteen`, `Hostel`, `Library`, `Transport`, `Others`.

## Existing database
The app creates missing tables and adds the nullable `department` column to an existing SQLite `user` table if absent. Existing users are preserved; existing `admin` users remain global administrators. Back up `instance/civicdesk.sqlite` before upgrading.

## Access control
Department scope is enforced in Flask routes, not just hidden in the UI. Department admins are restricted on dashboard queries, analytics, complaint detail pages, and status update endpoints.
