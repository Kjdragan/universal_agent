import { redirect } from "next/navigation";

export default function FilesPage() {
  redirect("/storage?tab=explorer");
}
