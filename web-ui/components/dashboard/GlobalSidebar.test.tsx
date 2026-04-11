import type { AnchorHTMLAttributes, ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GlobalSidebar } from "./GlobalSidebar";

const linkProps = vi.hoisted(() => [] as Array<Record<string, unknown>>);

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard/events",
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    prefetch,
    ...props
  }: AnchorHTMLAttributes<HTMLAnchorElement> & { children: ReactNode; prefetch?: boolean }) => {
    linkProps.push({ href, prefetch });
    return (
      <a href={String(href)} data-prefetch={String(prefetch)} {...props}>
        {children}
      </a>
    );
  },
}));

describe("GlobalSidebar", () => {
  beforeEach(() => {
    linkProps.length = 0;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders from caller-owned state without fetching capabilities", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<GlobalSidebar ownerId="owner_test" showCorporationNav />);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText("owner_test")).toBeInTheDocument();
    expect(screen.getByText("Corporation")).toBeInTheDocument();
  });

  it("opts dashboard links out of route prefetching", () => {
    render(<GlobalSidebar ownerId="owner_test" />);

    const internalLinks = linkProps.filter((props) => String(props.href || "").startsWith("/dashboard"));

    expect(internalLinks.length).toBeGreaterThan(0);
    expect(internalLinks.every((props) => props.prefetch === false)).toBe(true);
  });
});
