import Overview from "./pages/Overview";
import Settings from "./pages/Settings";

// add new pages here, the sidebar and router both read from this list
export const routes = [
	{ path: "/", label: "Overview", element: <Overview /> },
	{ path: "/settings", label: "Settings", element: <Settings /> }
];