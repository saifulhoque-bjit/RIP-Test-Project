const GIT_HOSTS = ["github.com", "gitlab.com", "bitbucket.org"];
const SHAREPOINT_SUFFIX = "sharepoint.com";

export function isSupportedLinkUrl(linkUrl: string): boolean {
  try {
    const parsedUrl = new URL(linkUrl);
    if (!["http:", "https:"].includes(parsedUrl.protocol)) {
      return false;
    }

    const hostname = parsedUrl.hostname.toLowerCase();
    const pathname = parsedUrl.pathname.toLowerCase();

    const isGitLink =
      GIT_HOSTS.some(
        (host) => hostname === host || hostname.endsWith(`.${host}`),
      ) ||
      hostname.startsWith("git.") ||
      pathname.endsWith(".git");

    const isSharepointLink =
      hostname === SHAREPOINT_SUFFIX ||
      hostname.endsWith(`.${SHAREPOINT_SUFFIX}`);

    return isGitLink || isSharepointLink;
  } catch {
    return false;
  }
}
