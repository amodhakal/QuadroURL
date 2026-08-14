import { zodResolver } from "@hookform/resolvers/zod"
import { Link2 } from "lucide-react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"
import { z } from "zod"

import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useCreateUrl } from "@/hooks/use-urls"
import { useUsers } from "@/hooks/use-users"
import { ApiError } from "@/lib/api"
import type { Url } from "@/types/api"

const formSchema = z.object({
  title: z.string().trim().min(1, "Title is required").max(120, "Title is too long"),
  original_url: z.string().trim().min(1, "URL is required").url("Enter a valid URL"),
  user_id: z.string().min(1, "Select an owner"),
})

type FormValues = z.infer<typeof formSchema>

export function UrlForm({
  onCreated,
}: {
  onCreated?: (url: Url) => void
}) {
  const { data: users } = useUsers()
  const createUrl = useCreateUrl()

  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      title: "",
      original_url: "",
      user_id: "",
    },
  })

  async function onSubmit(values: FormValues) {
    try {
      const url = await createUrl.mutateAsync({
        title: values.title,
        original_url: values.original_url,
        user_id: Number(values.user_id),
      })
      toast.success("Short link created")
      form.reset()
      onCreated?.(url)
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "Failed to create short link",
      )
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="grid gap-4">
        <FormField
          control={form.control}
          name="title"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Title</FormLabel>
              <FormControl>
                <Input placeholder="e.g. Project documentation" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="original_url"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Original URL</FormLabel>
              <FormControl>
                <Input
                  placeholder="https://example.com/very/long/path"
                  inputMode="url"
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="user_id"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Owner</FormLabel>
              <Select onValueChange={field.onChange} value={field.value}>
                <FormControl>
                  <SelectTrigger>
                    <SelectValue placeholder="Select a user" />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {users?.sample.map((user) => (
                    <SelectItem key={user.id} value={String(user.id)}>
                      {user.username}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button type="submit" disabled={createUrl.isPending} className="mt-2">
          {createUrl.isPending ? null : <Link2 />}
          {createUrl.isPending ? "Shortening…" : "Shorten link"}
        </Button>
      </form>
    </Form>
  )
}
