import { NavLink } from "react-router-dom";

import { routes } from "../routes";

export default function Sidebar() {
	return (
		<aside className="sidebar">
			<h1 className="sidebar-title">Dashboard</h1>

			<nav className="sidebar-nav">
				{routes.map((route) => (
					<NavLink
						key={route.path}
						to={route.path}
						end={route.path === "/"}
						className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}
					>
						{route.label}
					</NavLink>
				))}
			</nav>
		</aside>
	);
}