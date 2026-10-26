import { describe, expect, it } from "vitest";
import { phoneHref } from "../components/Linkified";

describe("phoneHref", () => {
  it("dials the main number and the extension after a pause", () => {
    expect(phoneHref("+7 (495) 133-00-60")).toBe("tel:+74951330060");
    expect(phoneHref("8 800 707 94 42")).toBe("tel:+78007079442");
    expect(phoneHref("+7 (495) 133-00-60 доб. 105")).toBe("tel:+74951330060,105");
    expect(phoneHref("8 (843) 555-01-23 (доп. 2)")).toBe("tel:+78435550123,2");
    expect(phoneHref("123")).toBeNull();
  });
});
