import axios from 'axios';

/**
 * Base axios instance for internal use only (e.g., multipart/form-data uploads).
 *
 * ⚠️  DO NOT use this client directly in components or pages.
 *     All data fetching must go through RTK Query hooks (src/services/api/).
 *
 * Authentication is handled by HttpOnly cookies, so requests must opt into
 * sending browser credentials to the API.
 */
const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '/api',
  timeout: 30000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

export default apiClient;
