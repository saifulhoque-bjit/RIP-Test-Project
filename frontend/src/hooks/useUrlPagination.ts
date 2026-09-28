import { useSearchParams } from "react-router-dom";

/**
 * Custom hook to manage pagination state through URL query parameters.
 * Automatically persists pagination state in the URL for deep linking and page reload support.
 *
 * @param paramName - URL query parameter name (default: "skip")
 * @returns Object containing skip value and setSkip function
 *
 * @example
 * const { skip, setSkip } = useUrlPagination();
 * // URL: /page?skip=10
 * // Access skip value and update pagination via setSkip(20)
 */
export function useUrlPagination(paramName: string = "skip") {
  const [searchParams, setSearchParams] = useSearchParams();

  const skip = parseInt(searchParams.get(paramName) ?? "0", 10);

  const setSkip = (newSkip: number) => {
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev);
      if (newSkip === 0) {
        params.delete(paramName);
      } else {
        params.set(paramName, newSkip.toString());
      }
      return params;
    });
  };

  return { skip, setSkip };
}
