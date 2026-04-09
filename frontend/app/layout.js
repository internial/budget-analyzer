import "./globals.css";

export const metadata = {
  title: "Budget Analyzer",
  description: "AI-assisted analysis for budget anomalies and risk signals.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
