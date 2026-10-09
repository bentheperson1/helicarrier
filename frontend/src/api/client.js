const base_url = "/api";

async function request(path, options = {}) {
	const response = await fetch(base_url + path, {
		headers: { "Content-Type": "application/json" },
		...options
	});

	if (!response.ok) {
		throw new Error(`Request failed: ${response.status} ${response.statusText}`);
	}

	return response.json();
}

export function api_get(path) {
	return request(path);
}

export function api_post(path, body) {
	return request(path, { method: "POST", body: JSON.stringify(body) });
}