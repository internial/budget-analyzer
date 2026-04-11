import "./globals.css";

export const metadata = {
  title: "Gov't Fraud, Waste & Abuse Detector",
  description: "AI-powered detection of fraud, waste, and abuse in government budget documents.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
