import { zodResolver } from "@hookform/resolvers/zod"
import { Link2 } from "lucide-react"
import { useState } from "react"
import { useForm, useWatch } from "react-hook-form"
import { toast } from "sonner"
import { z } from "zod"

import { LinkPreview } from "@/components/app/LinkPreview"
import { buildCampaignUrl, parseDestination, utmFields, type UtmValues } from "@/lib/link-tools"
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
  original_url: z.string().trim().min(1, "URL is required").refine(
    (value) => parseDestination(value) !== null,
    "Enter an HTTP or HTTPS URL without embedded credentials",
  ),
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
  const [campaignEnabled, setCampaignEnabled] = useState(false)
  const [campaign, setCampaign] = useState<UtmValues>({})

  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      title: "",
      original_url: "",
      user_id: "",
    },
  })

  const originalUrl = useWatch({ control: form.control, name: "original_url" })
  const title = useWatch({ control: form.control, name: "title" })
  const destination = buildCampaignUrl(originalUrl, campaignEnabled ? campaign : undefined)

  async function onSubmit(values: FormValues) {
    const originalUrl = buildCampaignUrl(
      values.original_url,
      campaignEnabled ? campaign : undefined,
    )
    if (!originalUrl) {
      form.setError("original_url", { message: "Enter a valid HTTP or HTTPS destination URL" })
      return
    }
    try {
      const url = await createUrl.mutateAsync({
        title: values.title,
        original_url: originalUrl,
        user_id: Number(values.user_id),
      })
      toast.success("Short link created")
      form.reset()
      setCampaign({})
      setCampaignEnabled(false)
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
        <fieldset className="space-y-4 rounded-xl border p-4" disabled={createUrl.isPending}>
          <legend className="px-1 text-sm font-medium">Campaign tracking</legend>
          <label className="flex cursor-pointer items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={campaignEnabled}
              onChange={(event) => setCampaignEnabled(event.target.checked)}
              className="size-4 accent-primary"
            />
            Add UTM parameters
          </label>
          {campaignEnabled && (
            <>
              <p id="utm-help" className="text-xs leading-relaxed text-muted-foreground">
                Blank fields keep the first existing UTM value. Filled fields replace it;
                duplicate UTM parameters are removed. Other values and fragments are preserved.
                Tracking may change query encoding; avoid using it on signed URLs.
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                {utmFields.map(({ key, label, placeholder }) => (
                  <div key={key} className="space-y-1.5">
                    <label htmlFor={key} className="text-sm font-medium">{label}</label>
                    <Input
                      id={key}
                      value={campaign[key] ?? ""}
                      placeholder={placeholder}
                      aria-describedby="utm-help"
                      onChange={(event) => {
                        const value = event.target.value
                        setCampaign((current) => ({ ...current, [key]: value }))
                      }}
                    />
                  </div>
                ))}
              </div>
            </>
          )}
        </fieldset>
        <LinkPreview url={destination ?? originalUrl} title={title} />
        <Button type="submit" disabled={createUrl.isPending} className="mt-2">
          {createUrl.isPending ? null : <Link2 />}
          {createUrl.isPending ? "Shortening…" : "Shorten link"}
        </Button>
      </form>
    </Form>
  )
}
