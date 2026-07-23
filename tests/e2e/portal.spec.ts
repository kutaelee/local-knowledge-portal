import { expect, test } from "@playwright/test";

test("overview and primary navigation", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Your knowledge, observable." })).toBeVisible();
  await page.getByRole("button", { name: "Explorer" }).click();
  await expect(page.getByRole("heading", { name: "Projects & documents" })).toBeVisible();
  await page.getByRole("button", { name: "Search" }).first().click();
  await expect(page.getByRole("heading", { name: "Search the source of truth" })).toBeVisible();
  await page.getByRole("button", { name: "Operations" }).click();
  await expect(page.getByRole("heading", { name: "Durable queue" })).toBeVisible();
});
