import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import StatCard from "../components/StatCard";
import { use_api } from "../hooks/use_api";

export default function Overview() {
	const summary = use_api("/stats/summary");
	const timeseries = use_api("/stats/timeseries?days=14");

	if (summary.error || timeseries.error) {
		return <p className="error">Failed to load: {summary.error || timeseries.error}</p>;
	}

	if (summary.loading || timeseries.loading) {
		return <p>Loading...</p>;
	}

	return (
		<div>
			<h2 className="page-title">Overview</h2>

			<section className="stat-grid">
				{summary.data.map((stat) => (
					<StatCard key={stat.id} {...stat} />
				))}
			</section>

			<section className="card chart-card">
				<h3>Last 14 days</h3>

				<ResponsiveContainer width="100%" height={300}>
					<LineChart data={timeseries.data}>
						<CartesianGrid stroke="#2a2f3a" strokeDasharray="3 3" />
						<XAxis dataKey="date" stroke="#8b93a5" />
						<YAxis stroke="#8b93a5" />
						<Tooltip />
						<Line type="monotone" dataKey="value" stroke="#5b8cff" strokeWidth={2} dot={false} />
					</LineChart>
				</ResponsiveContainer>
			</section>
		</div>
	);
}