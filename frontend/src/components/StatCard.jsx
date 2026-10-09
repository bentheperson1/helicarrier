export default function StatCard({ label, value, change, prefix = "", suffix = "" }) {
	const change_class = change >= 0 ? "positive" : "negative";
	const change_sign = change > 0 ? "+" : "";

	return (
		<div className="card stat-card">
			<span className="stat-label">{label}</span>
			<span className="stat-value">
				{prefix}{value.toLocaleString()}{suffix}
			</span>
			<span className={"stat-change " + change_class}>
				{change_sign}{change}%
			</span>
		</div>
	);
}