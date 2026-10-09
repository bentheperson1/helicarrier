import { useCallback, useEffect, useState } from "react";

import { api_get } from "../api/client";

export function use_api(path) {
	const [data, set_data] = useState(null);
	const [error, set_error] = useState(null);
	const [loading, set_loading] = useState(true);

	const load = useCallback(async () => {
		set_loading(true);
		set_error(null);

		try {
			set_data(await api_get(path));
		} catch (err) {
			set_error(err.message);
		} finally {
			set_loading(false);
		}
	}, [path]);

	useEffect(() => {
		load();
	}, [load]);

	return { data, error, loading, reload: load };
}