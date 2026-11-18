# MEDI-SYNC frontend — backend-connected build

This folder contains the frontend only. It does not include or replace your teammate's MongoDB database or FastAPI backend.

## 1. What I matched from your uploaded ZIP

The frontend uses the routes defined in `backend/main.py`:

- `POST /api/auth/login`, `POST /api/auth/register`
- `POST /api/organizations`, `GET /api/organizations`
- `GET /api/users`
- `GET/POST/PUT /api/medicines`
- `GET/POST/PUT /api/inventory`
- `POST /api/stock-in`, `POST /api/stock-out`
- `GET /api/batches`, `GET /api/stock-transactions`
- `GET/POST /api/medicine-requests`, `PUT /api/medicine-requests/{id}/decision`
- `GET/POST /api/suppliers`
- `GET/POST /api/purchase-orders`, `PUT /api/purchase-orders/{id}/status`
- `GET/POST/PUT /api/transfers`
- `GET/PUT /api/notifications`
- `GET /api/audit-logs`
- `GET /api/dashboard/summary`, `GET /api/dashboard/recent-activity`

The frontend does not create fake medicine, stock, user, supplier, or hospital records. Tables are filled from API responses. The organization registration and user registration are two separate backend operations, matching the actual API.

## 2. Run it

1. Extract this ZIP.
2. Open the `medisync_frontend` folder in VS Code.
3. Open `config.js`. The default is `http://127.0.0.1:8000`.
4. Start your teammate's FastAPI backend and MongoDB using their instructions.
5. In VS Code, open `index.html` with Live Server (usually `http://127.0.0.1:5500`).
6. Check the login screen. It should say **Backend & database connected** when `/api/health` succeeds.
7. If your backend runs on another port, change `API_BASE_URL` in `config.js`.

Do not open the HTML as a `file://` URL. Use Live Server so the backend CORS origin matches the backend's default allowed origins.

## 3. First-time setup

1. Click **Register organization**.
2. Create an organization. Save the returned MongoDB organization ID.
3. Click **Create account**.
4. Enter the organization ID and select one of the backend roles.
5. Sign in with the created email and password.

## 4. Important backend gaps found in the uploaded ZIP

Please share these with the backend teammate before treating this as a complete multi-role production system:

- The backend currently supports only these five roles: `admin`, `inventory_manager`, `doctor`, `staff`, and `procurement`. It does not define the seven distinct roles originally requested (Super Admin, Hospital Admin, Pharmacy Manager, Pharmacist, Inventory/Store Staff, Doctor/Hospital Staff, Supplier). This frontend labels the five roles according to the backend's actual role values; it does not pretend that missing roles exist.
- The current API source does not define `/api/auth/me`. The frontend therefore restores the saved browser session locally; it does not validate a token on page refresh.
- The FastAPI route handlers in `main.py` do not currently use an authentication dependency or enforce role-based access control. The browser hides navigation by role, but browser-side hiding is NOT security. The backend must validate the JWT on protected routes, derive the user and organization from the token, and enforce permissions server-side.
- Several list endpoints accept an optional `organization_id`; the backend must enforce organization scoping server-side. Do not rely on the frontend query parameter to protect private inventory.
- The AI service in `AI/api.py` is a separate Flask application. Its current `app.run` uses port 8000, which conflicts with the FastAPI backend if both are started with defaults. Ask the AI teammate to expose it on another port (for example 8001) and allow the Live Server origin through CORS before wiring its endpoints into the UI.

## 5. What this frontend does not do

- It does not modify MongoDB or backend files.
- It does not generate demo records to make empty tables look populated.
- It does not claim AI forecasts are available from the FastAPI server. The separate AI service needs a distinct configured URL and integration.
- It does not replace backend security. Follow the security notes above.

## 6. Troubleshooting

- **Backend not reachable:** start FastAPI and confirm `http://127.0.0.1:8000/api/health` opens.
- **Database disconnected:** the backend health route will return an error; ask the backend teammate to check MongoDB URI and database availability.
- **CORS error:** serve this folder with Live Server at port 5500, or ask the backend teammate to add your exact frontend origin to `FRONTEND_ORIGINS`.
- **Invalid organization ID:** use the exact `id` returned by the Register organization response.
- **Login fails:** create the user account first, then sign in with its email and password.
