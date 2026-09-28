import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { ToastContainer } from "@/components/common/ToastContainer";
import routes from "@/routes";

// Create router once outside component
const router = createBrowserRouter(routes);

export default function App() {
  return (
    <>
      <RouterProvider router={router} />
      <ToastContainer />
    </>
  );
}
